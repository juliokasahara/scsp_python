"""
login_dialog.py — Janela de login para o SCSP Video Analyst

Usa PyQt5 e chama auth.login() para autenticar no backend Java.
"""

import threading
from typing import Optional

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QPixmap, QIcon
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QCheckBox, QMessageBox,
    QSizePolicy, QWidget, QFrame,
)

from auth import login, UsuarioAutenticado, AuthError, SemPlanoError


# --------------------------------------------------------------------------- #
#  Worker: faz o login em thread separada para não travar a UI
# --------------------------------------------------------------------------- #
class _LoginWorker(QThread):
    sucesso = pyqtSignal(object)   # UsuarioAutenticado
    erro    = pyqtSignal(str, bool)  # mensagem, é_sem_credito

    def __init__(self, email: str, senha: str):
        super().__init__()
        self.email = email
        self.senha = senha

    def run(self):
        try:
            usuario = login(self.email.strip(), self.senha)
            self.sucesso.emit(usuario)
        except SemPlanoError as exc:
            self.erro.emit(str(exc), True)
        except AuthError as exc:
            self.erro.emit(str(exc), False)
        except Exception as exc:
            self.erro.emit(f"Erro inesperado: {exc}", False)


# --------------------------------------------------------------------------- #
#  Diálogo de login
# --------------------------------------------------------------------------- #
class LoginDialog(QDialog):
    """
    Exibe a tela de login e, ao autenticar com sucesso, armazena
    self.usuario com os dados do UsuarioAutenticado.
    """

    _STYLE = """
        QDialog {
            background-color: #1a1a2e;
        }
        QLabel#titulo {
            color: #e0e0e0;
            font-size: 22px;
            font-weight: bold;
        }
        QLabel#subtitulo {
            color: #888;
            font-size: 12px;
        }
        QLabel {
            color: #c8c8c8;
            font-size: 13px;
        }
        QLineEdit {
            background-color: #16213e;
            border: 1px solid #0f3460;
            border-radius: 6px;
            padding: 8px 10px;
            color: #e0e0e0;
            font-size: 13px;
        }
        QLineEdit:focus {
            border: 1px solid #e94560;
        }
        QPushButton#btn_login {
            background-color: #e94560;
            color: white;
            border: none;
            border-radius: 6px;
            padding: 10px;
            font-size: 14px;
            font-weight: bold;
        }
        QPushButton#btn_login:hover {
            background-color: #c73652;
        }
        QPushButton#btn_login:disabled {
            background-color: #555;
            color: #999;
        }
        QCheckBox {
            color: #888;
            font-size: 12px;
        }
        QFrame#separator {
            color: #0f3460;
        }
        QLabel#creditos {
            color: #e94560;
            font-size: 11px;
        }
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.usuario: Optional[UsuarioAutenticado] = None
        self._worker: Optional[_LoginWorker] = None
        self._setup_ui()

    # ── UI ──────────────────────────────────────────────────────────────── #
    def _setup_ui(self):
        self.setWindowTitle("LASTPOINT — Login")
        self.setFixedSize(380, 480)
        self.setStyleSheet(self._STYLE)
        self.setWindowFlags(Qt.Dialog | Qt.WindowCloseButtonHint)

        root = QVBoxLayout(self)
        root.setContentsMargins(40, 30, 40, 30)
        root.setSpacing(0)

        # Título
        lbl_titulo = QLabel("LASTPOINT")
        lbl_titulo.setObjectName("titulo")
        lbl_titulo.setAlignment(Qt.AlignCenter)
        root.addWidget(lbl_titulo)

        lbl_sub = QLabel("Video Analyst")
        lbl_sub.setObjectName("subtitulo")
        lbl_sub.setAlignment(Qt.AlignCenter)
        root.addWidget(lbl_sub)

        root.addSpacing(28)

        # Campo e-mail
        lbl_email = QLabel("E-mail")
        root.addWidget(lbl_email)
        root.addSpacing(4)

        self.input_email = QLineEdit()
        self.input_email.setPlaceholderText("seu@email.com")
        self.input_email.returnPressed.connect(self._tentar_login)
        root.addWidget(self.input_email)

        root.addSpacing(16)

        # Campo senha
        lbl_senha = QLabel("Senha")
        root.addWidget(lbl_senha)
        root.addSpacing(4)

        self.input_senha = QLineEdit()
        self.input_senha.setPlaceholderText("••••••••")
        self.input_senha.setEchoMode(QLineEdit.Password)
        self.input_senha.returnPressed.connect(self._tentar_login)
        root.addWidget(self.input_senha)

        root.addSpacing(8)

        # Mostrar senha
        self.chk_mostrar = QCheckBox("Mostrar senha")
        self.chk_mostrar.toggled.connect(
            lambda v: self.input_senha.setEchoMode(
                QLineEdit.Normal if v else QLineEdit.Password
            )
        )
        root.addWidget(self.chk_mostrar)

        root.addSpacing(24)

        # Botão Entrar
        self.btn_login = QPushButton("Entrar")
        self.btn_login.setObjectName("btn_login")
        self.btn_login.setCursor(Qt.PointingHandCursor)
        self.btn_login.clicked.connect(self._tentar_login)
        root.addWidget(self.btn_login)

        root.addSpacing(16)

        # Label de status / erro
        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("creditos")
        self.lbl_status.setAlignment(Qt.AlignCenter)
        self.lbl_status.setWordWrap(True)
        root.addWidget(self.lbl_status)

        root.addStretch()

        # Rodapé
        lbl_rodape = QLabel(
            "Acesso por plano diário: Básico (2), Plus (6) ou Ilimitado."
        )
        lbl_rodape.setObjectName("subtitulo")
        lbl_rodape.setAlignment(Qt.AlignCenter)
        lbl_rodape.setWordWrap(True)
        root.addWidget(lbl_rodape)

    # ── Lógica de login ──────────────────────────────────────────────────── #
    def _tentar_login(self):
        email = self.input_email.text().strip()
        senha = self.input_senha.text()

        if not email or not senha:
            self.lbl_status.setText("Preencha e-mail e senha.")
            return

        self._set_carregando(True)
        self.lbl_status.setText("Autenticando…")

        self._worker = _LoginWorker(email, senha)
        self._worker.sucesso.connect(self._on_sucesso)
        self._worker.erro.connect(self._on_erro)
        self._worker.start()

    def _on_sucesso(self, usuario: UsuarioAutenticado):
        self._set_carregando(False)
        self.usuario = usuario
        self.accept()  # encerra o diálogo com QDialog.Accepted

    def _on_erro(self, mensagem: str, sem_plano: bool):
        self._set_carregando(False)
        self.lbl_status.setText(mensagem)
        if sem_plano:
            QMessageBox.warning(
                self,
                "Sem plano de acesso",
                mensagem + "\n\nEntre em contato com o administrador.",
            )

    def _set_carregando(self, carregando: bool):
        self.btn_login.setEnabled(not carregando)
        self.input_email.setEnabled(not carregando)
        self.input_senha.setEnabled(not carregando)
        self.btn_login.setText("Aguarde…" if carregando else "Entrar")
