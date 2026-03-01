"""
usage_control.py — Controle diário de abertura de vídeos (server-side)

O contador e o limite são gerenciados pelo backend Java na tabela
`movimento_acesso`. Este módulo é apenas uma camada de chamada REST.

Endpoints utilizados:
  GET  /api/movimento-acesso/hoje          → consulta uso atual do dia
  POST /api/movimento-acesso/registrar-abertura → incrementa contador
"""

import requests
from typing import TYPE_CHECKING, Optional, Dict, Any

from config import BACKEND_URL

if TYPE_CHECKING:
    from auth import UsuarioAutenticado


# --------------------------------------------------------------------------- #
#  Exceções locais
# --------------------------------------------------------------------------- #

class LimiteAcessoError(Exception):
    """Limite diário atingido — retornado pelo backend (HTTP 422)."""


class PlanoInativoError(Exception):
    """Sem plano ativo para hoje — retornado pelo backend (HTTP 404)."""


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #

def _headers(usuario: "UsuarioAutenticado") -> dict:
    return {"Authorization": f"Bearer {usuario.access_token}"}


def _mensagem_erro(resp: requests.Response) -> str:
    try:
        errors = resp.json().get("errors", [])
        return errors[0] if errors else resp.text
    except Exception:
        return resp.text


# --------------------------------------------------------------------------- #
#  API pública
# --------------------------------------------------------------------------- #

def buscar_acesso_hoje(usuario: "UsuarioAutenticado", timeout: int = 8) -> Dict[str, Any]:
    """
    Consulta o registro de hoje para o usuário no servidor.

    Retorna o dict JSON completo de MovimentoAcessoDTO:
    { tipoPlano, planoLabel, quantidadeLimite, quantidadeUsada, restanteHoje, ... }

    Lança PlanoInativoError se não houver plano ativo hoje.
    """
    resp = requests.get(
        f"{BACKEND_URL}/movimento-acesso/hoje",
        headers=_headers(usuario),
        timeout=timeout,
    )
    if resp.status_code == 404:
        raise PlanoInativoError(_mensagem_erro(resp))
    resp.raise_for_status()
    return resp.json()


def pode_abrir(usuario: "UsuarioAutenticado") -> bool:
    """
    Retorna True se o usuário ainda pode abrir um vídeo hoje.
    Usa o valor de `restante_hoje` já carregado no objeto (sem chamada extra).
    """
    if usuario.ilimitado:
        return True
    return (usuario.restante_hoje or 0) > 0


def registrar_abertura(usuario: "UsuarioAutenticado", timeout: int = 8) -> Dict[str, Any]:
    """
    Comunica ao servidor que um vídeo foi aberto.
    Atualiza `usuario.restante_hoje` com o valor retornado pelo backend.

    Retorna o dict JSON do MovimentoAcessoDTO atualizado.
    Lança LimiteAcessoError (HTTP 422) se o limite foi atingido.
    Lança PlanoInativoError (HTTP 404) se não há plano ativo hoje.
    """
    resp = requests.post(
        f"{BACKEND_URL}/movimento-acesso/registrar-abertura",
        headers=_headers(usuario),
        timeout=timeout,
    )
    if resp.status_code == 422:
        raise LimiteAcessoError(_mensagem_erro(resp))
    if resp.status_code == 404:
        raise PlanoInativoError(_mensagem_erro(resp))
    resp.raise_for_status()

    data = resp.json()
    # Atualiza o campo local para que a UI reflita sem nova chamada
    usuario.restante_hoje = data.get("restanteHoje")
    return data
