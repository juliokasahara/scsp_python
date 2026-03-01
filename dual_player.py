import os
import sys
import cv2
from PyQt5.QtWidgets import (
    QApplication, QWidget, QPushButton,
    QHBoxLayout, QVBoxLayout, QFileDialog,
    QSlider, QLabel, QSizePolicy, QGraphicsView, QGraphicsScene, QFrame,
    QMenu, QAction, QActionGroup, QGraphicsPixmapItem, QMessageBox,
)
from PyQt5.QtCore import Qt, QTime, QPoint, QObject, QTimer, QThread, pyqtSignal
from PyQt5.QtGui import QPen, QColor, QImage, QPixmap, QIcon
from PyQt5.QtWidgets import QGraphicsLineItem

# ── Autenticação ──────────────────────────────────────────────────────────── #
try:
    from login_dialog import LoginDialog
    from auth import UsuarioAutenticado
    from usage_control import (
        pode_abrir, registrar_abertura,
        buscar_acesso_hoje,
        LimiteAcessoError, PlanoInativoError,
    )
    _AUTH_DISPONIVEL = True
except ImportError:
    _AUTH_DISPONIVEL = False

try:
    from s3_video_dialog import S3VideoDialog
    _S3_DIALOG_DISPONIVEL = True
except ImportError:
    _S3_DIALOG_DISPONIVEL = False

_HEARTBEAT_INTERVALO_MS  = 5 * 60 * 1000   # verifica a cada 5 minutos
_HEARTBEAT_FALHAS_LIMITE = 3               # fecha após 3 erros de rede consecutivos


# ── Worker de heartbeat ───────────────────────────────────────────────────── #
class _HeartbeatWorker(QThread):
    """Verifica em background se o plano do usuário ainda está ativo."""
    ok           = pyqtSignal(object)  # dict MovimentoAcessoDTO atualizado
    falha_plano  = pyqtSignal(str)     # plano inativo/expirado → fechar app
    falha_rede   = pyqtSignal()        # erro de rede → contar tentativa

    def __init__(self, usuario):
        super().__init__()
        self._usuario = usuario

    def run(self):
        try:
            data = buscar_acesso_hoje(self._usuario)
            self.ok.emit(data)
        except PlanoInativoError as exc:
            self.falha_plano.emit(str(exc))
        except Exception:
            self.falha_rede.emit()


class CVVideoPlayer(QObject):
    """Backend de reprodução de vídeo usando OpenCV — sem dependência de DirectShow."""
    positionChanged = pyqtSignal(int)
    durationChanged = pyqtSignal(int)

    def __init__(self, pixmap_item):
        super().__init__()
        self.pixmap_item = pixmap_item
        self.cap = None
        self.fps = 25.0
        self.total_frames = 0
        self.current_frame = 0
        self._playing = False
        self._playback_rate = 1.0
        self._muted = True
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance_frame)

    def setMedia(self, path):
        self._timer.stop()
        if self.cap:
            self.cap.release()
        self.cap = cv2.VideoCapture(path)
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 25.0
        self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.current_frame = 0
        self._playing = False
        self._render_frame(0)
        self.durationChanged.emit(self._ms(self.total_frames))
        self.positionChanged.emit(0)

    def play(self):
        if self.cap and not self._playing:
            self._playing = True
            self._timer.start(max(1, int(1000 / self.fps / self._playback_rate)))

    def pause(self):
        self._playing = False
        self._timer.stop()

    def setPosition(self, ms):
        if not self.cap:
            return
        frame_idx = max(0, min(int(ms / 1000.0 * self.fps), self.total_frames - 1))
        self.current_frame = frame_idx
        self._render_frame(frame_idx)
        self.positionChanged.emit(ms)

    def position(self):
        return self._ms(self.current_frame)

    def duration(self):
        return self._ms(self.total_frames)

    def setPlaybackRate(self, rate):
        self._playback_rate = max(0.01, rate)
        if self._playing:
            self._timer.setInterval(max(1, int(1000 / self.fps / self._playback_rate)))

    def setMuted(self, muted):
        self._muted = muted

    def _advance_frame(self):
        if not self.cap or self.current_frame >= self.total_frames - 1:
            self.pause()
            return
        # Leitura sequencial — muito mais rápida que seek a cada frame
        ret, frame = self.cap.read()
        if not ret:
            self.pause()
            return
        self.current_frame += 1
        self._draw_frame(frame)
        self.positionChanged.emit(self._ms(self.current_frame))

    def _render_frame(self, frame_idx):
        """Seek para um frame específico (usado pelo slider e setPosition)."""
        if not self.cap:
            return
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = self.cap.read()
        if not ret:
            return
        self._draw_frame(frame)

    def _draw_frame(self, frame):
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, _ = frame_rgb.shape
        qimg = QImage(frame_rgb.tobytes(), w, h, w * 3, QImage.Format_RGB888)
        self.pixmap_item.setPixmap(QPixmap.fromImage(qimg))

    def _ms(self, frame_idx):
        return int(frame_idx / self.fps * 1000) if self.fps > 0 else 0

class PanGraphicsView(QGraphicsView):
    def __init__(self, scene):
        super().__init__(scene)
        self.setFrameShape(QFrame.NoFrame)
        self.setFrameShadow(QFrame.Plain)
        self.setLineWidth(0)
        self.setDragMode(QGraphicsView.NoDrag)
        self._panning = False
        self._drawing = False
        self._pan_start = QPoint()
        self._last_draw_pos = QPoint()
        self._drawn_lines = []
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.StrongFocus)
        
        self.draw_color = QColor(255, 0, 0) 
        self.draw_width = 3  
        self.draw_mode = "free" 
        
        self._straight_start_pos = QPoint()
        self._current_straight_line = None
        self._current_circle = None

    def wheelEvent(self, event):
        """Implementa zoom centrado na posição do mouse com Ctrl + scroll"""
        if event.modifiers() & Qt.ControlModifier:
            zoom_factor = 1.1 if event.angleDelta().y() > 0 else 1/1.1
            
            mouse_pos = event.pos()
            
            scene_pos = self.mapToScene(mouse_pos)
            
            self.scale(zoom_factor, zoom_factor)
            
            new_mouse_pos = self.mapFromScene(scene_pos)
            
            delta = new_mouse_pos - mouse_pos
            
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() + delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() + delta.y())
            
            event.accept()
        else:
            super().wheelEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self._panning = True
            self._pan_start = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
        elif event.button() == Qt.RightButton:
            self._drawing = True
            start_pos = self.mapToScene(event.pos())
            
            if self.draw_mode == "free":
                self._last_draw_pos = start_pos
            elif self.draw_mode in ["straight", "circle"]:
                self._straight_start_pos = start_pos
                
            self.setCursor(Qt.CrossCursor)
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning:
            delta = event.pos() - self._pan_start
            self._pan_start = event.pos()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
        elif self._drawing:
            current_pos = self.mapToScene(event.pos())
            pen = QPen(self.draw_color, self.draw_width)
            
            if self.draw_mode == "free":
                line = self.scene().addLine(
                    self._last_draw_pos.x(), self._last_draw_pos.y(),
                    current_pos.x(), current_pos.y(),
                    pen
                )
                self._drawn_lines.append(line)
                self._last_draw_pos = current_pos
                
            elif self.draw_mode == "straight":
                if self._current_straight_line:
                    self.scene().removeItem(self._current_straight_line)
                    
                self._current_straight_line = self.scene().addLine(
                    self._straight_start_pos.x(), self._straight_start_pos.y(),
                    current_pos.x(), current_pos.y(),
                    pen
                )
                
            elif self.draw_mode == "circle":
                if self._current_circle:
                    self.scene().removeItem(self._current_circle)
                
                dx = current_pos.x() - self._straight_start_pos.x()
                dy = current_pos.y() - self._straight_start_pos.y()
                radius = (dx**2 + dy**2)**0.5
                
                self._current_circle = self.scene().addEllipse(
                    self._straight_start_pos.x() - radius,
                    self._straight_start_pos.y() - radius,
                    radius * 2, radius * 2,
                    pen
                )
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self._panning = False
            self.setCursor(Qt.ArrowCursor)
        elif event.button() == Qt.RightButton:
            if self._drawing:
                if self.draw_mode == "straight" and self._current_straight_line:
                    self._drawn_lines.append(self._current_straight_line)
                    self._current_straight_line = None
                elif self.draw_mode == "circle" and self._current_circle:
                    self._drawn_lines.append(self._current_circle)
                    self._current_circle = None
                
            self._drawing = False
            self.setCursor(Qt.ArrowCursor)
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_E:
            self.clear_drawings()
        else:
            super().keyPressEvent(event)

    def clear_drawings(self):
        """Remove todas as linhas e formas desenhadas da cena"""
        if self._current_straight_line:
            self.scene().removeItem(self._current_straight_line)
            self._current_straight_line = None
            
        if self._current_circle:
            self.scene().removeItem(self._current_circle)
            self._current_circle = None
            
        for item in self._drawn_lines:
            if item.scene():  
                self.scene().removeItem(item)
        self._drawn_lines.clear()  


class DualVideoPlayer(QWidget):
    def __init__(self, usuario=None):
        super().__init__()
        self._usuario = usuario
        self.setWindowTitle(self._montar_titulo())
        _ico = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_icon.png")
        if os.path.exists(_ico):
            self.setWindowIcon(QIcon(_ico))
        self.lbl_user = None  # será criado em _build_ui()

        # ── Heartbeat ──────────────────────────────────────────────────── #
        self._heartbeat_falhas = 0
        self._heartbeat_worker = None
        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.timeout.connect(self._verificar_heartbeat)
        if self._usuario and _AUTH_DISPONIVEL:
            self._heartbeat_timer.start(_HEARTBEAT_INTERVALO_MS)
        
        self.resize(1280, 800)  
        self.setMinimumSize(580, 400) 
        
        self.frame_ms = 40      
        self.skip1s_ms = 1000    
        self.skip2s_ms = 2000    

        self.saved_position_player1 = None
        self.saved_position_player2 = None

        self.video_item1 = QGraphicsPixmapItem()
        scene1 = QGraphicsScene(self)
        scene1.addItem(self.video_item1)
        self.view1 = PanGraphicsView(scene1)
        self.view1.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.player1 = CVVideoPlayer(self.video_item1)
        self.player1.setMuted(True)

        self.video_item2 = QGraphicsPixmapItem()
        scene2 = QGraphicsScene(self)
        scene2.addItem(self.video_item2)
        self.view2 = PanGraphicsView(scene2)
        self.view2.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.player2 = CVVideoPlayer(self.video_item2)
        self.player2.setMuted(True)

        self.slider1 = QSlider(Qt.Horizontal); self.slider1.setRange(0, 0)
        self.slider2 = QSlider(Qt.Horizontal); self.slider2.setRange(0, 0)
        self.label_time1 = QLabel("00:00:00 / 00:00:00")
        self.label_time2 = QLabel("00:00:00 / 00:00:00")
        self.player1.positionChanged.connect(self.update_position1)
        self.player1.durationChanged.connect(self.update_duration1)
        self.slider1.sliderMoved.connect(self.player1.setPosition)
        self.player2.positionChanged.connect(self.update_position2)
        self.player2.durationChanged.connect(self.update_duration2)
        self.slider2.sliderMoved.connect(self.player2.setPosition)

        btn_play_pause_both = QPushButton("⏯️")
        btn_play_pause_both.setCheckable(True)
        btn_play_pause_both.setMaximumWidth(60)  
        btn_play_pause_both.setMaximumHeight(30) 
        btn_play_pause_both.clicked.connect(lambda c: self.toggle_play_both(c, btn_play_pause_both))
        
        btn_prev_frame = QPushButton("⏪")
        btn_prev_frame.setMaximumWidth(60)
        btn_prev_frame.setMaximumHeight(30)
        btn_prev_frame.clicked.connect(self.prev_frame)
        
        btn_frame = QPushButton("⏩")
        btn_frame.setMaximumWidth(60)
        btn_frame.setMaximumHeight(30)
        btn_frame.clicked.connect(self.next_frame)
        
        btn_rew1 = QPushButton("⏪ 1s")
        btn_rew1.setMaximumWidth(50)
        btn_rew1.setMaximumHeight(30)
        btn_rew1.clicked.connect(self.rewind_1s)
        
        btn_rew2 = QPushButton("⏪ 2s")
        btn_rew2.setMaximumWidth(50)
        btn_rew2.setMaximumHeight(30)
        btn_rew2.clicked.connect(self.rewind_2s)
        
        btn_fwd1 = QPushButton("⏩ 1s")
        btn_fwd1.setMaximumWidth(50)
        btn_fwd1.setMaximumHeight(30)
        btn_fwd1.clicked.connect(self.forward_1s)
        
        btn_fwd2 = QPushButton("⏩ 2s")
        btn_fwd2.setMaximumWidth(50)
        btn_fwd2.setMaximumHeight(30)
        btn_fwd2.clicked.connect(self.forward_2s)
        
        btn_zoom_all_in = QPushButton("🔍+")
        btn_zoom_all_in.setMaximumWidth(50)
        btn_zoom_all_in.setMaximumHeight(30)
        btn_zoom_all_in.clicked.connect(lambda: (self.zoom_view(self.view1, 1.1), self.zoom_view(self.view2, 1.1)))
        
        btn_zoom_all_out = QPushButton("🔍-")
        btn_zoom_all_out.setMaximumWidth(50)
        btn_zoom_all_out.setMaximumHeight(30)
        btn_zoom_all_out.clicked.connect(lambda: (self.zoom_view(self.view1, 1/1.1), self.zoom_view(self.view2, 1/1.1)))
        
        btn_draw_config = QPushButton("🎨")
        btn_draw_config.setMaximumWidth(40)
        btn_draw_config.setMaximumHeight(30)
        self.setup_draw_config_menu(btn_draw_config)
        
        self.btn_layout_toggle = QPushButton("📺")
        self.btn_layout_toggle.setMaximumWidth(40)
        self.btn_layout_toggle.setMaximumHeight(30)
        self.btn_layout_toggle.clicked.connect(self.toggle_video_layout)
        self.is_vertical_layout = True  
        
        self.speed_label = QLabel("Velocidade: 1.0x")
        self.speed_slider = QSlider(Qt.Horizontal); self.speed_slider.setRange(10, 200); self.speed_slider.setValue(100)
        self.speed_slider.setFixedHeight(20); self.speed_slider.valueChanged.connect(self.change_speed)

        btn_open1 = QPushButton("🎦"); btn_open1.clicked.connect(lambda: self.open_file(self.player1))
        btn_play1 = QPushButton("⏯️"); btn_play1.setCheckable(True); btn_play1.clicked.connect(lambda c: self.toggle_play(self.player1, btn_play1, c))
        btn_rotate1 = QPushButton("🔄"); btn_rotate1.clicked.connect(lambda: self.rotate_video(self.video_item1))
        btn_mute1 = QPushButton("🔊"); btn_mute1.setCheckable(True); btn_mute1.setChecked(True); btn_mute1.clicked.connect(lambda c: self.toggle_mute(self.player1, btn_mute1, c))
        btn_zoom_in1 = QPushButton("🔍+"); btn_zoom_in1.clicked.connect(lambda: self.zoom_view(self.view1, 1.1))
        btn_zoom_out1 = QPushButton("🔍-"); btn_zoom_out1.clicked.connect(lambda: self.zoom_view(self.view1, 1/1.1))
        btn_frame1 = QPushButton("⏩"); btn_frame1.clicked.connect(lambda: self.next_frame_single(self.player1))
        btn_frameprev1 = QPushButton("⏪"); btn_frameprev1.clicked.connect(lambda: self.prev_frame_single(self.player1))

        controls1 = QHBoxLayout(); controls1.setSpacing(5); controls1.setContentsMargins(0, 0, 0, 0)
        for w in (btn_open1, btn_play1, btn_frameprev1, btn_frame1, btn_rotate1, btn_mute1, btn_zoom_in1, btn_zoom_out1): controls1.addWidget(w)

        btn_open2 = QPushButton("🎦"); btn_open2.clicked.connect(lambda: self.open_file_s3(self.player2))
        btn_play2 = QPushButton("⏯️"); btn_play2.setCheckable(True); btn_play2.clicked.connect(lambda c: self.toggle_play(self.player2, btn_play2, c))
        btn_rotate2 = QPushButton("🔄"); btn_rotate2.clicked.connect(lambda: self.rotate_video(self.video_item2))
        btn_mute2 = QPushButton("🔊"); btn_mute2.setCheckable(True); btn_mute2.setChecked(True); btn_mute2.clicked.connect(lambda c: self.toggle_mute(self.player2, btn_mute2, c))
        btn_zoom_in2 = QPushButton("🔍+"); btn_zoom_in2.clicked.connect(lambda: self.zoom_view(self.view2, 1.1))
        btn_zoom_out2 = QPushButton("🔍-"); btn_zoom_out2.clicked.connect(lambda: self.zoom_view(self.view2, 1/1.1))
        btn_frame2 = QPushButton("⏩"); btn_frame2.clicked.connect(lambda: self.next_frame_single(self.player2))
        btn_frameprev2 = QPushButton("⏪"); btn_frameprev2.clicked.connect(lambda: self.prev_frame_single(self.player2))

        controls2 = QHBoxLayout(); controls2.setSpacing(5); controls2.setContentsMargins(0, 0, 0, 0)
        for w in (btn_open2, btn_play2, btn_frameprev2, btn_frame2, btn_rotate2, btn_mute2, btn_zoom_in2, btn_zoom_out2): controls2.addWidget(w)

        layout1 = QVBoxLayout(); layout1.setSpacing(0); layout1.setContentsMargins(0, 0, 0, 0); layout1.addWidget(self.view1)
        layout2 = QVBoxLayout(); layout2.setSpacing(0); layout2.setContentsMargins(0, 0, 0, 0); layout2.addWidget(self.view2)
        self.players_layout = QHBoxLayout(); self.players_layout.setSpacing(0); self.players_layout.setContentsMargins(0, 0, 0, 0); self.players_layout.addLayout(layout1, 1); self.players_layout.addLayout(layout2, 1)
        
        self.layout1 = layout1
        self.layout2 = layout2

        footer = QHBoxLayout(); footer.setSpacing(20); footer.setContentsMargins(0, 0, 0, 0)
        group1 = QVBoxLayout(); group1.setSpacing(5); group1.addLayout(controls1); time_row1 = QHBoxLayout(); time_row1.setSpacing(5); time_row1.setContentsMargins(0, 0, 0, 0); time_row1.addWidget(self.label_time1); time_row1.addWidget(self.slider1); group1.addLayout(time_row1)
        group2 = QVBoxLayout(); group2.setSpacing(5); group2.addLayout(controls2); time_row2 = QHBoxLayout(); time_row2.setSpacing(5); time_row2.setContentsMargins(0, 0, 0, 0); time_row2.addWidget(self.label_time2); time_row2.addWidget(self.slider2); group2.addLayout(time_row2)
        footer.addLayout(group1, stretch=1); footer.addLayout(group2, stretch=1)

        root = QVBoxLayout(); root.setContentsMargins(5, 5, 5, 5)
        top = QHBoxLayout(); top.setSpacing(5); top.setContentsMargins(0, 0, 0, 0)
        
        # ── info do usuário logado ─────────────────────────────────────── #
        if self._usuario:
            self.lbl_user = QLabel(self._info_usuario_texto())
            self.lbl_user.setStyleSheet("color: #aaa; font-size: 11px; padding-right: 6px;")
            top.addWidget(self.lbl_user)

        top.addStretch()
        
        for b in (btn_play_pause_both, btn_prev_frame, btn_frame, btn_rew1, btn_rew2, btn_fwd1, btn_fwd2, btn_zoom_all_in, btn_zoom_all_out, btn_draw_config, self.btn_layout_toggle): 
            top.addWidget(b)
        
        top.addStretch()
        
        root.addLayout(top); root.addWidget(self.speed_label); root.addWidget(self.speed_slider); root.addLayout(self.players_layout, stretch=1); root.addLayout(footer)
        self.setLayout(root)

    def save_frame_player1(self):
        self.saved_position_player1 = self.player1.position()
        self.btn_restore_frame.setEnabled(True)

    def save_frame_player2(self):
        self.saved_position_player2 = self.player2.position()
        self.btn_restore_frame2.setEnabled(True)

    def restore_frame_player1(self):
        if self.saved_position_player1 is not None:
            self.player1.setPosition(self.saved_position_player1)

    def restore_frame_player2(self):
        if self.saved_position_player2 is not None:
            self.player2.setPosition(self.saved_position_player2)

    def _montar_titulo(self) -> str:
        titulo = "LASTPOINT_MOVIMENTO"
        if self._usuario and _AUTH_DISPONIVEL:
            rest = self._usuario.restante_hoje
            if rest is None:
                titulo += f"  —  {self._usuario.username}  |  Ilimitado"
            else:
                titulo += f"  —  {self._usuario.username}  |  {rest} video(s) restante(s) hoje"
        return titulo

    def _info_usuario_texto(self) -> str:
        if not self._usuario or not _AUTH_DISPONIVEL:
            return ""
        rest = self._usuario.restante_hoje
        plano = self._usuario.plano_label
        if rest is None:
            return f"👤 {self._usuario.username}  |  Plano: {plano} ∞"
        return f"👤 {self._usuario.username}  |  {plano}  |  Restam hoje: {rest}"

    def _atualizar_info_usuario(self):
        """Atualiza label e título após cada abertura de vídeo."""
        self.setWindowTitle(self._montar_titulo())
        if self.lbl_user is not None:
            self.lbl_user.setText(self._info_usuario_texto())

    # ── Heartbeat ──────────────────────────────────────────────────────── #

    def _verificar_heartbeat(self):
        """Dispara verificação do plano em background."""
        if not self._usuario or not _AUTH_DISPONIVEL:
            return
        self._heartbeat_worker = _HeartbeatWorker(self._usuario)
        self._heartbeat_worker.ok.connect(self._on_heartbeat_ok)
        self._heartbeat_worker.falha_plano.connect(self._on_heartbeat_falha_plano)
        self._heartbeat_worker.falha_rede.connect(self._on_heartbeat_falha_rede)
        self._heartbeat_worker.start()

    def _on_heartbeat_ok(self, data):
        self._heartbeat_falhas = 0
        self._usuario.restante_hoje = data.get("restanteHoje")
        self._atualizar_info_usuario()

    def _on_heartbeat_falha_plano(self, mensagem):
        self._heartbeat_timer.stop()
        QMessageBox.critical(
            self,
            "Sessão encerrada",
            "Seu plano de acesso foi encerrado ou expirou.\n\n"
            "O programa será fechado.\n"
            "Contate o administrador para renovar seu plano.",
        )
        self.close()

    def _on_heartbeat_falha_rede(self):
        self._heartbeat_falhas += 1
        if self._heartbeat_falhas >= _HEARTBEAT_FALHAS_LIMITE:
            self._heartbeat_timer.stop()
            QMessageBox.critical(
                self,
                "Sem conexão com o servidor",
                f"Não foi possível verificar seu plano por "
                f"{_HEARTBEAT_FALHAS_LIMITE} tentativas consecutivas.\n\n"
                "O programa será fechado por segurança.\n"
                "Verifique sua conexão com o servidor LASTPOINT.",
            )
            self.close()

    def closeEvent(self, event):
        self._heartbeat_timer.stop()
        self.player1.pause()
        self.player2.pause()
        super().closeEvent(event)

    def _verificar_limite(self) -> bool:
        """Verifica e exibe aviso se o limite diário foi atingido. Retorna False se bloqueado."""
        if self._usuario and _AUTH_DISPONIVEL:
            if not pode_abrir(self._usuario):
                limite = self._usuario.limite_diario
                QMessageBox.warning(
                    self,
                    "Limite diário atingido",
                    f"Você já usou todas as {limite} abertura(s) do dia.\n"
                    f"Plano: {self._usuario.plano_label}\n\n"
                    "O contador é zerado à meia-noite pelo servidor.",
                )
                return False
        return True

    def _registrar_e_abrir(self, player, path: str):
        """Registra abertura no backend e carrega o vídeo no player."""
        if self._usuario and _AUTH_DISPONIVEL:
            try:
                registrar_abertura(self._usuario)
            except LimiteAcessoError as exc:
                QMessageBox.warning(self, "Limite diário atingido", str(exc))
                return
            except Exception as exc:
                QMessageBox.warning(
                    self, "Aviso",
                    f"Não foi possível registrar abertura no servidor:\n{exc}\n\n"
                    "O vídeo será aberto assim mesmo."
                )
        player.setMedia(path)
        if self._usuario and _AUTH_DISPONIVEL:
            self._atualizar_info_usuario()

    def open_file(self, player):
        """Abre arquivo local (comportamento original — usado pelo player 1)."""
        if not self._verificar_limite():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Selecione um vídeo", "",
            "Vídeos (*.mp4 *.avi *.mov *.mkv *.webm *.flv *.wmv *.m4v);;Todos os arquivos (*)"
        )
        if not path:
            return
        self._registrar_e_abrir(player, path)

    def open_file_s3(self, player):
        """Abre modal com opção de arquivo local ou vídeo do MinIO S3."""
        if not self._verificar_limite():
            return
        if not _S3_DIALOG_DISPONIVEL:
            # fallback para diálogo local se s3_video_dialog não carregar
            self.open_file(player)
            return
        dlg = S3VideoDialog(usuario=self._usuario, parent=self)
        if dlg.exec_() == S3VideoDialog.Accepted and dlg.selected_path:
            self._registrar_e_abrir(player, dlg.selected_path)

    def toggle_play(self, player, button, checked):
        if checked: player.play(); button.setText("⏯️")
        else: player.pause(); button.setText("⏯️")

    def toggle_play_both(self, checked, button):
        if checked: self.player1.play(); self.player2.play(); button.setText("⏯️")
        else: self.player1.pause(); self.player2.pause(); button.setText("⏯️")

    def next_frame(self):
        pos1 = self.player1.position() + self.frame_ms
        pos2 = self.player2.position() + self.frame_ms
        self.player1.setPosition(pos1)
        self.player2.setPosition(pos2)

    def prev_frame(self):
        pos1 = self.player1.position() - self.frame_ms
        pos2 = self.player2.position() - self.frame_ms
        self.player1.setPosition(pos1)
        self.player2.setPosition(pos2)

    def next_frame_single(self, player):
        player.setPosition(player.position() + self.frame_ms)

    def prev_frame_single(self, player):
        player.setPosition(player.position() - self.frame_ms)

    def rewind_1s(self):
        for p in (self.player1, self.player2): p.setPosition(max(0, p.position() - self.skip1s_ms))

    def rewind_2s(self):
        for p in (self.player1, self.player2): p.setPosition(max(0, p.position() - self.skip2s_ms))

    def forward_1s(self):
        for p in (self.player1, self.player2): p.setPosition(min(p.duration(), p.position() + self.skip1s_ms))

    def forward_2s(self):
        for p in (self.player1, self.player2): p.setPosition(min(p.duration(), p.position() + self.skip2s_ms))

    def zoom_view(self, view, factor): view.scale(factor, factor)

    def change_speed(self, v):
        rate = v / 100.0; self.speed_label.setText(f"Velocidade: {rate:.1f}x"); self.player1.setPlaybackRate(rate); self.player2.setPlaybackRate(rate)

    def rotate_video(self, item):
        center = item.boundingRect().center(); item.setTransformOriginPoint(center); item.setRotation((item.rotation() + 90) % 360)

    def toggle_mute(self, player, button, muted): player.setMuted(muted); button.setText("🔇 Mute" if muted else "🔊 Unmute")

    def ms_to_time(self, ms): return QTime(0, 0, 0).addMSecs(ms).toString("hh:mm:ss")

    def update_position1(self, pos):
        self.slider1.setValue(pos); d = self.player1.duration(); self.label_time1.setText(f"{self.ms_to_time(pos)} / {self.ms_to_time(d)}")

    def update_duration1(self, d): self.slider1.setRange(0, d)

    def update_position2(self, pos):
        self.slider2.setValue(pos); d = self.player2.duration(); self.label_time2.setText(f"{self.ms_to_time(pos)} / {self.ms_to_time(d)}")

    def update_duration2(self, d): self.slider2.setRange(0, d)

    def setup_draw_config_menu(self, button):
        """Configura o menu de opções de desenho"""
        menu = QMenu(self)
        
        mode_menu = menu.addMenu("✏️ Modo")
        mode_group = QActionGroup(self)
        
        modes = [
            ("🖊️ Desenho Livre", "free"),
            ("📏 Linha Reta", "straight"),
            ("⭕ Círculo", "circle")
        ]
        
        for name, mode in modes:
            action = QAction(name, self)
            action.setCheckable(True)
            action.setChecked(mode == "free")  # Desenho livre como padrão
            action.triggered.connect(lambda checked, m=mode: self.set_draw_mode(m))
            mode_group.addAction(action)
            mode_menu.addAction(action)
        
        menu.addSeparator()
        
        width_menu = menu.addMenu("📏 Largura")
        width_group = QActionGroup(self)
        
        widths = [0.3, 0.8, 1, 2, 3]
        for width in widths:
            action = QAction(f"{width}px", self)
            action.setCheckable(True)
            action.setChecked(width == 0.8)  # como padrão
            action.triggered.connect(lambda checked, w=width: self.set_draw_width(w))
            width_group.addAction(action)
            width_menu.addAction(action)
        
        menu.addSeparator()
        
        color_menu = menu.addMenu("🎨 Cores")
        color_group = QActionGroup(self)
        
        colors = [
            ("🔴 Vermelho", QColor(255, 0, 0)),
            ("🟢 Verde", QColor(0, 255, 0)),
            ("🔵 Azul", QColor(0, 0, 255)),
            ("🟡 Amarelo", QColor(255, 255, 0)),
            ("🟠 Laranja", QColor(255, 165, 0)),
            ("🟣 Roxo", QColor(128, 0, 128)),
            ("⚫ Preto", QColor(0, 0, 0)),
            ("⚪ Branco", QColor(255, 255, 255))
        ]
        
        for name, color in colors:
            action = QAction(name, self)
            action.setCheckable(True)
            action.setChecked(color == QColor(255, 0, 0))  # Vermelho como padrão
            action.triggered.connect(lambda checked, c=color: self.set_draw_color(c))
            color_group.addAction(action)
            color_menu.addAction(action)
        
        button.setMenu(menu)

    def set_draw_mode(self, mode):
        """Define o modo de desenho para ambas as views"""
        self.view1.draw_mode = mode
        self.view2.draw_mode = mode

    def set_draw_width(self, width):
        """Define a largura do traço para ambas as views"""
        self.view1.draw_width = width
        self.view2.draw_width = width

    def set_draw_color(self, color):
        """Define a cor do traço para ambas as views"""
        self.view1.draw_color = color
        self.view2.draw_color = color

    def toggle_video_layout(self):
        """Alterna entre layout horizontal (lado a lado) e vertical (um acima do outro)"""
        while self.players_layout.count():
            child = self.players_layout.takeAt(0)
            if child.layout():
                child.layout().setParent(None)
        
        if self.is_vertical_layout:
            self.players_layout.setDirection(QHBoxLayout.LeftToRight)
            self.players_layout.addLayout(self.layout1, 1)
            self.players_layout.addLayout(self.layout2, 1)
            self.btn_layout_toggle.setText("📱")
            self.is_vertical_layout = False
        else:
            self.players_layout.setDirection(QVBoxLayout.TopToBottom)
            self.players_layout.addLayout(self.layout1, 1)
            self.players_layout.addLayout(self.layout2, 1)
            self.btn_layout_toggle.setText("📺")
            self.is_vertical_layout = True

if __name__ == "__main__":
    # Define AppUserModelID para o Windows mostrar o ícone correto na taskbar
    # (sem isso o Windows agrupa sob o ícone do python.exe)
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("scsp.dual_player")
    except Exception:
        pass

    app = QApplication(sys.argv)
    _app_ico = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_icon.png")
    if os.path.exists(_app_ico):
        app.setWindowIcon(QIcon(_app_ico))

    # ── Tela de login ──────────────────────────────────────────────────── #
    usuario_autenticado = None
    if _AUTH_DISPONIVEL:
        dlg = LoginDialog()
        resultado = dlg.exec_()
        if resultado != LoginDialog.Accepted or dlg.usuario is None:
            # Usuário fechou ou não autenticou — encerra o app
            sys.exit(0)
        usuario_autenticado = dlg.usuario
    else:
        # Se os módulos de auth não estiverem disponíveis, avisa e continua
        QMessageBox.warning(
            None,
            "Módulo de autenticação ausente",
            "Não foi possível carregar o módulo de login.\n"
            "Instale as dependências: pip install requests",
        )

    # ── Janela principal ───────────────────────────────────────────────── #
    player = DualVideoPlayer(usuario=usuario_autenticado)
    player.showMaximized()
    sys.exit(app.exec_())
