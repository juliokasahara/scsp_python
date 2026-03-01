# ==============================================================
# Configurações do backend SCSP
# Altere BACKEND_URL para apontar para o servidor correto.
# ==============================================================

# URL base do backend Java (sem barra final)
# Exemplos:
#   Produção:  "https://api.seudominio.com.br/api"
#   Local:     "http://localhost:8080/api"
BACKEND_URL = "http://localhost:8080/api"

# Credenciais do OAuth2 client — mesmo client usado pelo Angular no backend Java
# NÃO é o client_id do Google. É o client registrado no AuthorizationServerConfig.
OAUTH_CLIENT_ID = "my-angular-app"

# ⚠️  O CLIENT_SECRET é o valor da variável de ambiente JWT_PASSWORD no servidor.
#     Para descobrir o valor em dev, veja o arquivo scsp-java/jwt_password.txt
#     ou a variável JWT_PASSWORD configurada no seu ambiente/Docker.
#     Em produção, leia de variável de ambiente:
#       import os; OAUTH_CLIENT_SECRET = os.environ.get("JWT_PASSWORD", "")
OAUTH_CLIENT_SECRET = "@321"  # JWT_PASSWORD do .env

# ==============================================================
# Planos de acesso
# Os limites e tipo de plano são gerenciados pelo backend Java
# na tabela `movimento_acesso`. Não é necessário configurar aqui.
# Para ativar um plano para um usuário, use o endpoint admin:
#   POST /api/movimento-acesso/admin/ativar-plano
# ==============================================================

# ==============================================================
# MinIO / S3 — pasta de cache local para vídeos baixados
# As credenciais S3 ficam SOMENTE no servidor Java.
# O cliente Python usa apenas a API autenticada (JWT).
# ==============================================================
import os as _os

# Pasta local onde os vídeos baixados ficam em cache
S3_CACHE_DIR = _os.path.join(_os.path.expanduser("~"), ".lastpoint", "videos")

# Pasta local onde os thumbnails gerados ficam em cache
S3_THUMB_DIR = _os.path.join(_os.path.expanduser("~"), ".lastpoint", "thumbs")

# ==============================================================
# Google OAuth2 — Login com Google
# Lê de variável de ambiente; fallback para o arquivo usado pelo
# backend Java (scsp-java/google_secret.txt) ou valor padrão vazio.
#
# Para configurar:
#   Opção 1 (recomendada): defina variáveis de ambiente antes de rodar
#     set GOOGLE_CLIENT_ID=566842041415-...apps.googleusercontent.com
#     set GOOGLE_CLIENT_SECRET=GOCSPX-...
#   Opção 2: o arquivo scsp-java/google_secret.txt já contém o secret.
# ==============================================================
def _read_file_secret(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""

_JAVA_ROOT = _os.path.join(_os.path.dirname(__file__), "..", "scsp-java")

GOOGLE_CLIENT_ID = _os.environ.get(
    "GOOGLE_CLIENT_ID",
    "566842041415-nvmk4ll352svs9sc1gflvphva9hddegb.apps.googleusercontent.com",
)

GOOGLE_CLIENT_SECRET = (
    _os.environ.get("GOOGLE_CLIENT_SECRET")
    or _read_file_secret(_os.path.join(_JAVA_ROOT, "google_secret.txt"))
    or ""  # falha explícita em runtime se vazio
)
