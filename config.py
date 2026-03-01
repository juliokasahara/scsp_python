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
