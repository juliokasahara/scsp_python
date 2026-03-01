"""
auth.py — Autenticação com o backend Java (OAuth2 + plano de acesso diário)

Fluxo:
  1. POST /api/oauth/token              → recebe access_token JWT
  2. GET  /api/usuarios/perfil           → nome e dados de exibição
  3. GET  /api/movimento-acesso/hoje     → plano ativo, limite e uso do dia
     Se 404  → SemPlanoError (não tem plano ativo hoje)
"""

import base64
import requests
from dataclasses import dataclass, field
from typing import Optional, List

from config import BACKEND_URL, OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET


# --------------------------------------------------------------------------- #
#  Dados do usuário autenticado
# --------------------------------------------------------------------------- #

@dataclass
class UsuarioAutenticado:
    access_token: str
    username: str
    email: str
    roles: List[str]
    plano_role: str            # ex: "PLANO_6"
    plano_label: str           # ex: "Plus"
    limite_diario: Optional[int]  # None = ilimitado
    restante_hoje: Optional[int]  # None = ilimitado; atualizado após cada abertura
    id_usuario: Optional[int] = None
    imagem_path: Optional[str] = None
    extra: dict = field(default_factory=dict)

    @property
    def ilimitado(self) -> bool:
        return self.limite_diario is None


# --------------------------------------------------------------------------- #
#  Exceções
# --------------------------------------------------------------------------- #

class AuthError(Exception):
    """Erro de autenticação/autorização."""


class SemPlanoError(AuthError):
    """Login OK mas sem plano de acesso ativo para hoje."""
    def __init__(self, detalhe: str = ""):
        super().__init__(
            "Seu usuário não possui um plano de acesso ativo para hoje.\n\n"
            "Entre em contato com o administrador para ativar\n"
            "o plano Básico (2/dia), Plus (6/dia) ou Ilimitado."
            + (f"\n\nDetalhe: {detalhe}" if detalhe else "")
        )


# --------------------------------------------------------------------------- #
#  Helpers internos
# --------------------------------------------------------------------------- #

def _basic_auth_header() -> str:
    credentials = f"{OAUTH_CLIENT_ID}:{OAUTH_CLIENT_SECRET}"
    encoded = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
    return f"Basic {encoded}"


def _decode_jwt_roles(token: str) -> List[str]:
    """Decodifica authorities do JWT sem verificar assinatura."""
    import json
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return []
        padding = 4 - len(parts[1]) % 4
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * padding))
        return payload.get("authorities", [])
    except Exception:
        return []


# --------------------------------------------------------------------------- #
#  Função principal
# --------------------------------------------------------------------------- #

def login(email: str, senha: str, timeout: int = 10) -> UsuarioAutenticado:
    """
    Autentica e verifica plano ativo no servidor.
    Exceções: AuthError, SemPlanoError.
    """

    # ── 1. Token OAuth2 ────────────────────────────────────────────────── #
    try:
        resp = requests.post(
            f"{BACKEND_URL}/oauth/token",
            data={"grant_type": "password", "username": email, "password": senha},
            headers={
                "Authorization": _basic_auth_header(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=timeout,
        )
    except requests.exceptions.ConnectionError:
        raise AuthError(
            "Não foi possível conectar ao servidor.\n"
            "Verifique sua conexão ou o endereço do backend em config.py."
        )
    except requests.exceptions.Timeout:
        raise AuthError("O servidor demorou para responder. Tente novamente.")

    if resp.status_code in (400, 403):
        raise AuthError("E-mail ou senha incorretos.")
    if resp.status_code == 401:
        raise AuthError(
            "Credenciais do cliente OAuth inválidas.\n"
            "Verifique OAUTH_CLIENT_SECRET em config.py."
        )
    if not resp.ok:
        raise AuthError(f"Erro no servidor ({resp.status_code}): {resp.text}")

    access_token = resp.json().get("access_token")
    if not access_token:
        raise AuthError("Resposta inesperada do servidor (sem access_token).")

    headers_auth = {"Authorization": f"Bearer {access_token}"}

    # ── 2. Perfil do usuário ────────────────────────────────────────────── #
    try:
        resp_perfil = requests.get(
            f"{BACKEND_URL}/usuarios/perfil",
            headers=headers_auth, timeout=timeout,
        )
    except requests.exceptions.RequestException as exc:
        raise AuthError(f"Erro ao buscar perfil: {exc}")

    if not resp_perfil.ok:
        raise AuthError(f"Erro ao buscar perfil ({resp_perfil.status_code}).")

    perfil = resp_perfil.json()

    # ── 3. Plano ativo do dia (movimento_acesso) ────────────────────────── #
    try:
        resp_acesso = requests.get(
            f"{BACKEND_URL}/movimento-acesso/hoje",
            headers=headers_auth, timeout=timeout,
        )
    except requests.exceptions.RequestException as exc:
        raise AuthError(f"Erro ao verificar plano: {exc}")

    if resp_acesso.status_code == 404:
        try:
            detalhe = resp_acesso.json().get("errors", [""])[0]
        except Exception:
            detalhe = ""
        raise SemPlanoError(detalhe)
    if not resp_acesso.ok:
        raise AuthError(f"Erro ao verificar plano ({resp_acesso.status_code}).")

    acesso = resp_acesso.json()

    return UsuarioAutenticado(
        access_token=access_token,
        username=perfil.get("username", email),
        email=perfil.get("email", email),
        roles=_decode_jwt_roles(access_token),
        plano_role=acesso.get("tipoPlano", ""),
        plano_label=acesso.get("planoLabel", ""),
        limite_diario=acesso.get("quantidadeLimite"),    # None = ilimitado
        restante_hoje=acesso.get("restanteHoje"),         # None = ilimitado
        id_usuario=perfil.get("idUsuario"),
        imagem_path=perfil.get("imagemPath"),
        extra=perfil,
    )
