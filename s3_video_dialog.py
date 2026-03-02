"""
s3_video_dialog.py
──────────────────
Modal chamado pelo btn_open2 que permite ao usuário escolher entre abrir um
arquivo local ou baixar um vídeo do MinIO/S3 via API segura do backend Java.

Fluxo seguro (sem expor credenciais S3 no cliente):
  1. GET  {BACKEND_URL}/videos               → lista vídeos (requer JWT)
  2. GET  {BACKEND_URL}/videos/presigned?key → URL temporária de 15 min
  3. Download direto pela presigned URL       → salvo no cache local

Dependências: requests (já utilizado pelo auth.py)
"""

from __future__ import annotations

import os
import datetime
from typing import Optional

import cv2
import requests

from PySide6.QtCore import Qt, QThread, Signal, QSize
from PySide6.QtGui import QPixmap, QImage, QIcon, QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QListWidget, QListWidgetItem, QProgressBar, QFileDialog,
    QMessageBox, QFrame, QWidget, QTabWidget, QSizePolicy, QListView,
    QSplitter, QTreeWidget, QTreeWidgetItem,
)

try:
    from config import BACKEND_URL, S3_CACHE_DIR, S3_THUMB_DIR
except ImportError:
    BACKEND_URL  = "http://localhost:8080/api"
    S3_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".lastpoint", "videos")
    S3_THUMB_DIR = os.path.join(os.path.expanduser("~"), ".lastpoint", "thumbs")


# ── Helpers ───────────────────────────────────────────────────────────────── #

def _fmt_size(n_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n_bytes < 1024:
            return f"{n_bytes:.1f} {unit}"
        n_bytes /= 1024
    return f"{n_bytes:.1f} TB"


def _auth_headers(usuario) -> dict:
    """Retorna cabeçalho Authorization com o JWT do usuário logado."""
    if usuario and hasattr(usuario, "access_token"):
        return {"Authorization": f"Bearer {usuario.access_token}"}
    return {}


def _local_cache_path(key: str) -> str:
    return os.path.join(S3_CACHE_DIR, key.replace("/", os.sep))


def _thumb_cache_path(key: str) -> str:
    """Caminho do thumbnail JPG cacheado para uma key do S3."""
    safe = key.replace("/", os.sep)
    return os.path.join(S3_THUMB_DIR, safe + ".jpg")


_THUMB_W, _THUMB_H = 192, 108  # 16:9


def _default_icon() -> QIcon:
    """Ícone cinza com símbolo de play para thumbnails ainda não gerados."""
    img = QImage(_THUMB_W, _THUMB_H, QImage.Format_RGB888)
    img.fill(QColor(50, 50, 50))
    px = QPixmap.fromImage(img)
    return QIcon(px)


def _pixmap_from_frame(frame) -> QPixmap:
    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    h, w, _ = frame_rgb.shape
    qimg = QImage(frame_rgb.tobytes(), w, h, w * 3, QImage.Format_RGB888)
    return QPixmap.fromImage(qimg).scaled(
        _THUMB_W, _THUMB_H, Qt.KeepAspectRatio, Qt.SmoothTransformation
    )


def _extract_frame_from_file(path: str):
    """Abre arquivo de vídeo local e retorna o frame a ~10%."""
    cap = cv2.VideoCapture(path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    seek  = max(0, min(int(total * 0.1), total - 1))
    cap.set(cv2.CAP_PROP_POS_FRAMES, seek)
    ret, frame = cap.read()
    cap.release()
    return frame if ret else None


# ── Worker unificado de thumbnail ─────────────────────────────────────────── #
# Prioridade:
#   1. Thumbnail JPG já cacheado  → carrega direto
#   2. Vídeo completo já baixado  → extrai frame localmente
#   3. Ainda só no S3             → solicita ao backend GET /videos/thumbnail
#                                    (jcodec no servidor gera o JPG e retorna
#                                     presigned URL — nunca baixa vídeo inteiro)

class _ThumbnailWorker(QThread):
    ready = Signal(str, QPixmap)  # key, pixmap

    def __init__(self, key: str, usuario):
        super().__init__()
        self._key     = key
        self._usuario = usuario

    def run(self):
        try:
            thumb_path = _thumb_cache_path(self._key)
            video_path = _local_cache_path(self._key)

            pixmap = None

            # 1. thumbnail já em cache local
            if os.path.exists(thumb_path):
                pixmap = QPixmap(thumb_path).scaled(
                    _THUMB_W, _THUMB_H, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )

            # 2. vídeo completo já baixado no disco
            elif os.path.exists(video_path):
                frame = _extract_frame_from_file(video_path)
                if frame is not None:
                    pixmap = _pixmap_from_frame(frame)
                    self._save_thumb(pixmap, thumb_path)

            # 3. somente no S3 → pede ao backend para gerar/buscar o thumbnail
            else:
                pixmap = self._fetch_thumb_from_backend()
                if pixmap and not pixmap.isNull():
                    self._save_thumb(pixmap, thumb_path)

            if pixmap and not pixmap.isNull():
                self.ready.emit(self._key, pixmap)

        except Exception:
            pass  # silencia — ícone padrão fica no lugar

    def _fetch_thumb_from_backend(self):
        """
        Solicita ao backend a presigned URL do thumbnail.
        O servidor usa jcodec para gerar o JPG a partir do vídeo
        completo e o armazena em S3 em thumbs/{key}.jpg.
        """
        resp = requests.get(
            f"{BACKEND_URL}/videos/thumbnail",
            params={"key": self._key},
            headers=_auth_headers(self._usuario),
            timeout=60,  # geração pode demorar para vídeos grandes
        )
        if resp.status_code != 200:
            return None

        thumb_url = resp.json().get("url")
        if not thumb_url:
            return None

        # baixa a imagem JPG (pequena — ~10-30 KB)
        img_resp = requests.get(thumb_url, timeout=15)
        if img_resp.status_code != 200:
            return None

        pixmap = QPixmap()
        pixmap.loadFromData(img_resp.content)
        return pixmap.scaled(_THUMB_W, _THUMB_H, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    @staticmethod
    def _save_thumb(pixmap: QPixmap, path: str):
        """Salva o thumbnail em disco para cache futuro."""
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            pixmap.save(path, "JPEG", 85)
        except Exception:
            pass


# ── Worker: lista vídeos via backend ─────────────────────────────────────── #

class _ListWorker(QThread):
    done  = Signal(list)
    error = Signal(str)

    def __init__(self, usuario):
        super().__init__()
        self._usuario = usuario

    def run(self):
        try:
            resp = requests.get(
                f"{BACKEND_URL}/videos",
                headers=_auth_headers(self._usuario),
                timeout=15,
            )
            resp.raise_for_status()
            self.done.emit(resp.json())
        except Exception as exc:
            self.error.emit(str(exc))


# ── Worker: obtém presigned URL e faz o download ─────────────────────────── #

class _DownloadWorker(QThread):
    progress = Signal(int)   # 0–100
    done     = Signal(str)   # caminho local
    error    = Signal(str)

    def __init__(self, key: str, local_path: str, usuario):
        super().__init__()
        self._key        = key
        self._local_path = local_path
        self._usuario    = usuario

    def run(self):
        try:
            # 1. Solicita a presigned URL ao backend (com JWT)
            resp = requests.get(
                f"{BACKEND_URL}/videos/presigned",
                params={"key": self._key},
                headers=_auth_headers(self._usuario),
                timeout=15,
            )
            resp.raise_for_status()
            presigned_url = resp.json()["url"]

            # 2. Baixa o arquivo diretamente do MinIO pela presigned URL
            #    (sem credenciais — a URL já autoriza o acesso por 15 min)
            os.makedirs(os.path.dirname(self._local_path), exist_ok=True)
            with requests.get(presigned_url, stream=True, timeout=120) as dl:
                dl.raise_for_status()
                total = int(dl.headers.get("Content-Length", 0))
                downloaded = 0
                with open(self._local_path, "wb") as f:
                    for chunk in dl.iter_content(chunk_size=256 * 1024):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            self.progress.emit(int(downloaded / total * 100))

            self.done.emit(self._local_path)

        except Exception as exc:
            if os.path.exists(self._local_path):
                os.remove(self._local_path)
            self.error.emit(str(exc))


# ── Aba Nuvem ─────────────────────────────────────────────────────────────── #

class _CloudTab(QWidget):
    video_ready = Signal(str)

    def __init__(self, usuario=None, parent=None):
        super().__init__(parent)
        self._usuario           = usuario
        self._objects           = []   # lista completa do backend
        self._list_worker       = None
        self._download_worker   = None
        self._thumb_workers     = []
        self._key_to_pixmap     = {}   # key → QPixmap (cache em memória)
        self._current_prefix    = None # None = todos; str = prefixo de pasta
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        # ── barra superior ─────────────────────────────────────────────── #
        top = QHBoxLayout()
        self.lbl_status = QLabel("Clique em 🔄 para listar os vídeos do servidor.")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.btn_refresh = QPushButton("🔄 Atualizar")
        self.btn_refresh.setFixedWidth(110)
        self.btn_refresh.clicked.connect(self._load_list)
        top.addWidget(self.lbl_status, 1)
        top.addWidget(self.btn_refresh)
        lay.addLayout(top)

        # ── splitter: árvore de pastas | grade de thumbnails ───────────── #
        splitter = QSplitter(Qt.Horizontal)

        # árvore de pastas (esquerda)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(140)
        self.tree.setMaximumWidth(260)
        self.tree.setStyleSheet(
            "QTreeWidget { font-size: 12px; } "
            "QTreeWidget::item { padding: 4px 2px; }"
        )
        self.tree.currentItemChanged.connect(self._on_tree_selection)
        splitter.addWidget(self.tree)

        # grade de thumbnails (direita)
        self.list_widget = QListWidget()
        self.list_widget.setViewMode(QListView.ViewMode.IconMode)
        self.list_widget.setIconSize(QSize(_THUMB_W, _THUMB_H))
        self.list_widget.setGridSize(QSize(_THUMB_W + 20, _THUMB_H + 44))
        self.list_widget.setResizeMode(QListView.ResizeMode.Adjust)
        self.list_widget.setMovement(QListView.Movement.Static)
        self.list_widget.setSpacing(8)
        self.list_widget.setWordWrap(True)
        self.list_widget.setTextElideMode(Qt.ElideMiddle)
        self.list_widget.itemDoubleClicked.connect(self._on_double_click)
        splitter.addWidget(self.list_widget)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        lay.addWidget(splitter, 1)

        # ── rodapé ────────────────────────────────────────────────────── #
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        lay.addWidget(self.progress_bar)

        self.lbl_download = QLabel("")
        self.lbl_download.setStyleSheet("color: #888; font-size: 11px;")
        self.lbl_download.setVisible(False)
        lay.addWidget(self.lbl_download)

        self.btn_open = QPushButton("⬇️  Baixar e Abrir")
        self.btn_open.setEnabled(False)
        self.btn_open.clicked.connect(self._start_download)
        lay.addWidget(self.btn_open)

        self.list_widget.itemSelectionChanged.connect(
            lambda: self.btn_open.setEnabled(len(self.list_widget.selectedItems()) > 0)
        )

    # ── carregamento da lista ─────────────────────────────────────────────── #

    def _load_list(self):
        self.btn_refresh.setEnabled(False)
        self.lbl_status.setText("⏳ Conectando ao servidor…")
        self.list_widget.clear()
        self._objects = []

        self._list_worker = _ListWorker(self._usuario)
        self._list_worker.done.connect(self._on_list_done)
        self._list_worker.error.connect(self._on_list_error)
        self._list_worker.start()

    def _on_list_done(self, objects):
        self._objects = objects
        self._key_to_pixmap = {}
        self.btn_refresh.setEnabled(True)

        if not objects:
            self.lbl_status.setText("Nenhum vídeo encontrado no servidor.")
            self.tree.clear()
            self.list_widget.clear()
            return

        self.lbl_status.setText(
            f"✅  {len(objects)} vídeo(s) disponível(eis). Duplo-clique para baixar."
        )

        # ── constrói árvore de pastas ──────────────────────────────────── #
        self.tree.clear()
        folder_nodes = {}  # prefixo → QTreeWidgetItem

        # item raiz: Todos os vídeos
        root_item = QTreeWidgetItem([f"☁️  Todos  ({len(objects)})"])
        root_item.setData(0, Qt.UserRole, None)  # None = sem filtro
        self.tree.addTopLevelItem(root_item)

        for obj in objects:
            key   = obj["key"]
            parts = key.split("/")
            # partes até o penúltimo elemento são pastas
            current_path = ""
            parent_node  = root_item
            for folder in parts[:-1]:
                current_path = f"{current_path}/{folder}" if current_path else folder
                if current_path not in folder_nodes:
                    node = QTreeWidgetItem([f"📁  {folder}"])
                    node.setData(0, Qt.UserRole, current_path)
                    parent_node.addChild(node)
                    folder_nodes[current_path] = node
                parent_node = folder_nodes[current_path]

        self.tree.expandAll()
        # atualiza contadores nas pastas
        self._update_folder_counts(folder_nodes)

        # seleciona raiz (mostra todos)
        self.tree.setCurrentItem(root_item)

        # dispara thumbnails para todos os objetos
        for obj in objects:
            self._request_thumbnail(obj["key"])

    def _update_folder_counts(self, folder_nodes: dict):
        """Atualiza o texto dos nós de pasta com contagem de vídeos."""
        for prefix, node in folder_nodes.items():
            count = sum(
                1 for obj in self._objects
                if obj["key"].startswith(prefix + "/")
            )
            folder = node.text(0).split("  ")[1].split("  (")[0]  # nome limpo
            node.setText(0, f"📁  {folder}  ({count})")

    def _on_list_error(self, msg):
        self.btn_refresh.setEnabled(True)
        self.lbl_status.setText(f"❌ Erro ao conectar: {msg}")

    def _on_tree_selection(self, current, _previous):
        """Filtra a grade ao selecionar uma pasta na árvore."""
        if current is None:
            return
        prefix = current.data(0, Qt.UserRole)  # None = todos
        self._populate_grid(prefix)

    def _populate_grid(self, prefix):
        """Preenche a grade com os vídeos do prefixo de pasta dado."""
        self._current_prefix = prefix
        self.list_widget.clear()
        self.btn_open.setEnabled(False)

        if not self._objects:
            return

        if prefix is None:
            filtered = self._objects
        else:
            filtered = [obj for obj in self._objects
                        if obj["key"].startswith(prefix + "/")]

        default_icon = _default_icon()
        for obj in filtered:
            key    = obj["key"]
            name   = os.path.basename(key)
            size   = _fmt_size(obj.get("size", 0))
            dt_raw = obj.get("lastModified", "")
            try:
                dt     = datetime.datetime.fromisoformat(dt_raw.replace("Z", "+00:00"))
                dt_str = dt.strftime("%d/%m/%Y %H:%M")
            except Exception:
                dt_str = dt_raw[:16]

            cached = os.path.exists(_local_cache_path(key))
            label  = f"{name}\n{size}  {dt_str}"
            icon   = QIcon(self._key_to_pixmap[key]) if key in self._key_to_pixmap else default_icon

            item = QListWidgetItem(icon, label)
            item.setData(Qt.UserRole, obj)
            item.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            if cached:
                item.setForeground(Qt.darkGreen)
                item.setToolTip("💾 Já baixado — abre do cache local.")
            else:
                item.setToolTip("☁️  Clique duplo para baixar e abrir.")
            self.list_widget.addItem(item)

    def _request_thumbnail(self, key: str):
        """Dispara worker de thumbnail (cache local, vídeo local ou backend)."""
        w = _ThumbnailWorker(key, self._usuario)
        w.ready.connect(self._apply_thumbnail)
        w.start()
        self._thumb_workers.append(w)

    def _apply_thumbnail(self, key: str, pixmap: QPixmap):
        """Armazena o pixmap em memória e atualiza todos os itens visíveis."""
        self._key_to_pixmap[key] = pixmap
        icon = QIcon(pixmap)
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item and item.data(Qt.UserRole) and item.data(Qt.UserRole).get("key") == key:
                item.setIcon(icon)
                break

    # ── download ──────────────────────────────────────────────────────────── #

    def _selected_object(self):
        items = self.list_widget.selectedItems()
        return items[0].data(Qt.UserRole) if items else None

    def _on_double_click(self, _item):
        self._start_download()

    def _start_download(self):
        obj = self._selected_object()
        if not obj:
            return

        key   = obj["key"]
        local = _local_cache_path(key)

        # Já está em cache → abre direto sem baixar
        if os.path.exists(local):
            self.video_ready.emit(local)
            return

        self.btn_open.setEnabled(False)
        self.btn_refresh.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.lbl_download.setText(f"Baixando: {os.path.basename(key)} …")
        self.lbl_download.setVisible(True)

        self._download_worker = _DownloadWorker(key, local, self._usuario)
        self._download_worker.progress.connect(self.progress_bar.setValue)
        self._download_worker.done.connect(self._on_download_done)
        self._download_worker.error.connect(self._on_download_error)
        self._download_worker.start()

    def _on_download_done(self, local_path):
        self.progress_bar.setValue(100)
        self.lbl_download.setText("✅ Download concluído.")
        self.btn_open.setEnabled(True)
        self.btn_refresh.setEnabled(True)
        # atualiza cor do item na grade e regenera thumbnail
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item is None:
                continue
            obj = item.data(Qt.UserRole)
            if obj and _local_cache_path(obj["key"]) == local_path:
                item.setForeground(Qt.darkGreen)
                item.setToolTip("💾 Já baixado — abre do cache local.")
                self._request_thumbnail(obj["key"])
                break
        self.video_ready.emit(local_path)

    def _on_download_error(self, msg):
        self.progress_bar.setVisible(False)
        self.lbl_download.setVisible(False)
        self.btn_open.setEnabled(True)
        self.btn_refresh.setEnabled(True)
        QMessageBox.critical(self, "Erro no download",
                             f"Não foi possível baixar o vídeo:\n\n{msg}")


# ── Diálogo principal ─────────────────────────────────────────────────────── #

class S3VideoDialog(QDialog):
    """
    Modal com duas abas:
      • "📁 Arquivo Local"   – QFileDialog padrão
      • "☁️  Vídeos na Nuvem" – lista/baixa via API do backend (JWT + presigned URL)
    """

    def __init__(self, usuario=None, parent=None):
        super().__init__(parent)
        # remove o botão "?" da barra de título
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setWindowTitle("Abrir Vídeo")
        self.setMinimumSize(900, 600)
        self.selected_path: Optional[str] = None
        self._usuario = usuario
        self._build_ui()
        # Abre com 80% da tela, respeitando o mínimo definido acima
        if parent:
            screen = parent.screen().availableGeometry()
        else:
            from PySide6.QtWidgets import QApplication
            screen = QApplication.primaryScreen().availableGeometry()
        self.resize(max(1100, int(screen.width() * 0.80)),
                    max(680,  int(screen.height() * 0.80)))

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        self.tabs = QTabWidget()

        # ── Aba Local ─────────────────────────────────────────────────── #
        local_tab = QWidget()
        local_lay = QVBoxLayout(local_tab)
        local_lay.setAlignment(Qt.AlignCenter)

        lbl = QLabel("Selecione um arquivo de vídeo no seu computador.")
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet("font-size: 13px; color: #ccc;")

        btn_browse = QPushButton("📂  Procurar arquivo…")
        btn_browse.setFixedHeight(42)
        btn_browse.setFixedWidth(200)
        btn_browse.setStyleSheet("font-size: 13px;")
        btn_browse.clicked.connect(self._browse_local)

        local_lay.addStretch()
        local_lay.addWidget(lbl)
        local_lay.addSpacing(16)
        local_lay.addWidget(btn_browse, alignment=Qt.AlignHCenter)
        local_lay.addStretch()

        # ── Aba Nuvem ─────────────────────────────────────────────────── #
        self._cloud_tab = _CloudTab(usuario=self._usuario)
        self._cloud_tab.video_ready.connect(self._accept_path)

        self.tabs.addTab(local_tab,       "📁  Arquivo Local")
        self.tabs.addTab(self._cloud_tab, "☁️   Vídeos na Nuvem")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        lay.addWidget(self.tabs, 1)

        # ── Rodapé ────────────────────────────────────────────────────── #
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        lay.addWidget(sep)

        footer = QHBoxLayout()
        self.lbl_selected = QLabel("Nenhum vídeo selecionado.")
        self.lbl_selected.setStyleSheet("color: #888; font-size: 11px;")
        btn_cancel = QPushButton("Cancelar")
        btn_cancel.setFixedWidth(90)
        btn_cancel.clicked.connect(self.reject)
        footer.addWidget(self.lbl_selected, 1)
        footer.addWidget(btn_cancel)
        lay.addLayout(footer)

    def _on_tab_changed(self, index):
        # Aba 1 = Vídeos na Nuvem: carrega automaticamente na primeira vez
        if index == 1 and not self._cloud_tab._objects and not self._cloud_tab._list_worker:
            self._cloud_tab._load_list()

    def _browse_local(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Selecione um vídeo", "",
            "Vídeos (*.mp4 *.avi *.mov *.mkv *.webm *.flv *.wmv *.m4v);;Todos (*)"
        )
        if path:
            self._accept_path(path)

    def _accept_path(self, path: str):
        self.selected_path = path
        self.lbl_selected.setText(f"✅ {os.path.basename(path)}")
        self.accept()
