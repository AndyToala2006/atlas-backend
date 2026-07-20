"""Rutas de autenticación: registro, login y verificación del usuario."""
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select

from ..deps import Principal, get_current_user, get_current_user_db, get_db
from ..models import PerfilTono, Usuario
from ..schemas import LoginIn, RegistroIn, TokenOut, UsuarioOut
from ..security import create_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["Autenticación"])


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
def registrar(datos: RegistroIn, db=Depends(get_db)):
    existe = db.execute(select(Usuario).where(Usuario.email == datos.email)).scalar_one_or_none()
    if existe:
        raise HTTPException(status_code=409, detail="El email ya está registrado")

    usuario = Usuario(
        email=datos.email,
        nombre=datos.nombre,
        password_hash=hash_password(datos.password),
    )
    usuario.perfil_tono = PerfilTono(nombre=datos.tono, descripcion="Tono por defecto del usuario")
    db.add(usuario)
    db.commit()
    db.refresh(usuario)
    return TokenOut(access_token=create_token(usuario))


@router.post("/login", response_model=TokenOut)
def login(datos: LoginIn, db=Depends(get_db)):
    usuario = db.execute(select(Usuario).where(Usuario.email == datos.email)).scalar_one_or_none()
    if usuario is None or not verify_password(datos.password, usuario.password_hash):
        raise HTTPException(status_code=401, detail="Credenciales inválidas")
    return TokenOut(access_token=create_token(usuario))


@router.get("/me", response_model=UsuarioOut)
def perfil_optimizado(response: Response, user: Principal = Depends(get_current_user)):
    """OPTIMIZADO: identidad tomada del JWT. 0 consultas a la base de datos."""
    response.headers["X-Query-Count"] = "0"
    response.headers["X-Auth-Mode"] = "jwt-claims"
    return UsuarioOut(id=user.id, email=user.email, nombre=user.nombre)


@router.get("/me-db", response_model=UsuarioOut)
def perfil_ingenuo(response: Response, db=Depends(get_db), usuario: Usuario = Depends(get_current_user_db)):
    """INGENUO (para comparar): consulta la base en cada request. 1+ consultas."""
    response.headers["X-Query-Count"] = str(db.info.get("query_count", 0))
    response.headers["X-Auth-Mode"] = "db-lookup"
    return UsuarioOut(id=usuario.id, email=usuario.email, nombre=usuario.nombre)
