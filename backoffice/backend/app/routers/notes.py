from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app.database import async_session
from app.models import AppNote

router = APIRouter(prefix="/api/notes", tags=["notes"])


class NoteCreate(BaseModel):
    namespace: str
    app_name: str
    note: str


class NoteOut(BaseModel):
    id: int
    namespace: str
    app_name: str
    note: str
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


@router.get("", response_model=list[NoteOut])
async def list_notes(namespace: str | None = None, app_name: str | None = None):
    async with async_session() as session:
        q = select(AppNote).order_by(AppNote.updated_at.desc())
        if namespace:
            q = q.where(AppNote.namespace == namespace)
        if app_name:
            q = q.where(AppNote.app_name == app_name)
        result = await session.execute(q)
        return [
            NoteOut(
                id=n.id,
                namespace=n.namespace,
                app_name=n.app_name,
                note=n.note,
                created_at=n.created_at.isoformat(),
                updated_at=n.updated_at.isoformat(),
            )
            for n in result.scalars()
        ]


@router.post("", response_model=NoteOut, status_code=201)
async def create_note(body: NoteCreate):
    async with async_session() as session:
        note = AppNote(**body.model_dump())
        session.add(note)
        await session.commit()
        await session.refresh(note)
        return NoteOut(
            id=note.id,
            namespace=note.namespace,
            app_name=note.app_name,
            note=note.note,
            created_at=note.created_at.isoformat(),
            updated_at=note.updated_at.isoformat(),
        )


@router.delete("/{note_id}", status_code=204)
async def delete_note(note_id: int):
    async with async_session() as session:
        note = await session.get(AppNote, note_id)
        if note:
            await session.delete(note)
            await session.commit()
