import logging
import uuid

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from sqlalchemy import func, select

from app.db import SessionLocal
from app.deps import AdminUser, LLMDep, SessionDep, SettingsDep
from app.models import Document
from app.schemas import DocumentListOut, DocumentOut, TextDocumentIn
from app.services import ingest
from app.services.ingest import UnsupportedDocument
from app.services.storage import DocumentStorage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/documents", tags=["knowledge-base"])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def _to_out(doc: Document) -> DocumentOut:
    return DocumentOut(
        id=str(doc.id),
        title=doc.title,
        source_type=doc.source_type,
        school_code=doc.school_code,
        topic=doc.topic,
        filename=doc.filename,
        size_bytes=doc.size_bytes,
        status=doc.status,
        error=doc.error,
        chunk_count=doc.chunk_count,
        is_active=doc.is_active,
        created_at=doc.created_at,
        indexed_at=doc.indexed_at,
    )


async def _index_in_background(document_id: uuid.UUID, raw_text: str, llm, settings) -> None:
    """Chạy sau khi đã trả response — upload không phải chờ nhúng vector xong."""
    async with SessionLocal() as session:
        doc = await session.get(Document, document_id)
        if doc is None:
            return
        try:
            await ingest.index_document(session, llm, settings, doc, raw_text)
        except Exception:  # noqa: BLE001 - trạng thái lỗi đã ghi vào document.status
            logger.exception("Index nền thất bại cho tài liệu %s", document_id)


@router.post("", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_document(
    session: SessionDep,
    settings: SettingsDep,
    llm: LLMDep,
    user: AdminUser,
    background: BackgroundTasks,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    school_code: str | None = Form(default=None),
    topic: str | None = Form(default=None),
) -> DocumentOut:
    """Nạp tài liệu vào kho tri thức RAG. Hỗ trợ PDF, DOCX, MD, TXT."""
    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File rỗng")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File vượt quá {MAX_UPLOAD_BYTES // 1024 // 1024}MB",
        )

    filename = file.filename or "untitled"
    try:
        kind = ingest.detect_kind(filename, file.content_type)
        raw_text = ingest.extract_text(data, kind)
    except UnsupportedDocument as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Không đọc được file: {exc}") from exc

    if len(raw_text.strip()) < 50:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Không trích được nội dung văn bản (PDF scan ảnh chưa hỗ trợ, cần OCR trước)",
        )

    digest = ingest.checksum(data)
    if existing := await session.scalar(
        select(Document).where(Document.checksum == digest, Document.is_active.is_(True))
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Tài liệu trùng với '{existing.title}' (id={existing.id})",
        )

    doc = Document(
        id=uuid.uuid4(),
        title=(title or filename).strip()[:500],
        source_type="upload",
        school_code=school_code,
        topic=topic,
        filename=filename,
        content_type=file.content_type,
        size_bytes=len(data),
        checksum=digest,
        status="pending",
        uploaded_by=uuid.UUID(user.user_id) if _is_uuid(user.user_id) else None,
    )

    storage = DocumentStorage(settings)
    if storage.enabled:
        try:
            doc.s3_key = storage.put(f"documents/{doc.id}/{filename}", data, file.content_type)
        except Exception:  # noqa: BLE001 - lưu file gốc thất bại vẫn index được nội dung
            logger.exception("Lưu file gốc lên S3 thất bại")

    session.add(doc)
    await session.commit()
    await session.refresh(doc)

    background.add_task(_index_in_background, doc.id, raw_text, llm, settings)
    return _to_out(doc)


@router.post("/text", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def create_text_document(
    payload: TextDocumentIn,
    session: SessionDep,
    settings: SettingsDep,
    llm: LLMDep,
    user: AdminUser,
    background: BackgroundTasks,
) -> DocumentOut:
    """Nạp tri thức bằng văn bản dán thẳng — tiện để thử nhanh mà không cần file."""
    doc = Document(
        id=uuid.uuid4(),
        title=payload.title.strip()[:500],
        source_type="manual",
        school_code=payload.school_code,
        topic=payload.topic,
        checksum=ingest.checksum(payload.content.encode()),
        size_bytes=len(payload.content.encode()),
        status="pending",
        uploaded_by=uuid.UUID(user.user_id) if _is_uuid(user.user_id) else None,
    )
    session.add(doc)
    await session.commit()
    await session.refresh(doc)

    background.add_task(_index_in_background, doc.id, payload.content, llm, settings)
    return _to_out(doc)


@router.get("", response_model=DocumentListOut)
async def list_documents(
    session: SessionDep,
    user: AdminUser,
    status_filter: str | None = Query(default=None, alias="status"),
    school_code: str | None = None,
    topic: str | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
) -> DocumentListOut:
    stmt = select(Document).order_by(Document.created_at.desc())
    count_stmt = select(func.count(Document.id))
    for condition in (
        Document.status == status_filter if status_filter else None,
        Document.school_code == school_code if school_code else None,
        Document.topic == topic if topic else None,
    ):
        if condition is not None:
            stmt = stmt.where(condition)
            count_stmt = count_stmt.where(condition)

    rows = (await session.execute(stmt.limit(limit).offset(offset))).scalars().all()
    total = await session.scalar(count_stmt) or 0
    return DocumentListOut(items=[_to_out(d) for d in rows], total=total)


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(document_id: uuid.UUID, session: SessionDep, user: AdminUser) -> DocumentOut:
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy tài liệu")
    return _to_out(doc)


@router.get("/{document_id}/download")
async def download_document(
    document_id: uuid.UUID, session: SessionDep, settings: SettingsDep, user: AdminUser
) -> dict[str, str]:
    doc = await session.get(Document, document_id)
    if doc is None or not doc.s3_key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tài liệu không có file gốc")
    url = DocumentStorage(settings).presigned_url(doc.s3_key)
    if not url:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Chưa cấu hình S3")
    return {"url": url}


@router.post("/{document_id}/reindex", response_model=DocumentOut)
async def reindex_document(
    document_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    llm: LLMDep,
    user: AdminUser,
    background: BackgroundTasks,
) -> DocumentOut:
    """Index lại (dùng khi đổi model embedding hoặc cách cắt đoạn)."""
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy tài liệu")
    if not doc.s3_key:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Tài liệu không lưu file gốc nên không index lại được — hãy nạp lại nội dung",
        )
    try:
        data = DocumentStorage(settings).get(doc.s3_key)
        kind = ingest.detect_kind(doc.filename or "", doc.content_type)
        raw_text = ingest.extract_text(data, kind)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Không lấy lại được file gốc: {exc}"
        ) from exc

    doc.status = "pending"
    await session.commit()
    background.add_task(_index_in_background, doc.id, raw_text, llm, settings)
    return _to_out(doc)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID, session: SessionDep, settings: SettingsDep, user: AdminUser
) -> None:
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy tài liệu")
    if doc.s3_key:
        DocumentStorage(settings).delete(doc.s3_key)
    await session.delete(doc)  # chunks xóa theo cascade
    await session.commit()


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError):
        return False
