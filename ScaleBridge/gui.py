from __future__ import annotations
import json
import sys
from pathlib import Path
import numpy as np
from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QImageReader, QPixmap, QColor, QPen, QPainter, QCursor
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QPushButton, QLabel, QLineEdit, QComboBox, QDoubleSpinBox,
    QFileDialog, QMessageBox, QGraphicsView, QGraphicsScene, QGraphicsItem,
    QTableWidget, QTableWidgetItem, QHeaderView, QSplitter, QPlainTextEdit,
    QFormLayout, QGroupBox, QDialog, QDialogButtonBox, QScrollArea)
from core import Model, solve, CalculationError, export_colmap, export_metashape, write_json, VERSION
from markers import FAMILIES, validate_preset, detect_scale


class PhotoView(QGraphicsView):
    clicked=Signal(float,float)
    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        target=QPixmap(33,33); target.fill(Qt.GlobalColor.transparent)
        painter=QPainter(target); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # White outline keeps the target visible against both dark and light photos.
        for color,width in [('white',3),('black',1)]:
            painter.setPen(QPen(QColor(color),width))
            painter.drawEllipse(6,6,20,20)
            painter.drawLine(2,16,30,16); painter.drawLine(16,2,16,30)
        painter.end()
        self.viewport().setCursor(QCursor(target,16,16))
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.pix=None; self.marks=[]; self.start=None
        self.pan_last=None; self.panning=False
    def clear(self):
        self.scene().clear(); self.scene().setSceneRect(0,0,0,0)
        self.pix=None; self.marks=[]; self.start=None
        self.pan_last=None; self.panning=False
        self.resetTransform()
    def load(self,path,width,height):
        self.clear()
        reader=QImageReader(str(path))
        # Image geometry must match COLMAP; do not silently EXIF-rotate it.
        reader.setAutoTransform(False)
        image=reader.read()
        if image.isNull(): raise ValueError(f'画像を読み込めません: {path}\n{reader.errorString()}')
        if (image.width(),image.height())!=(width,height):
            raise ValueError(f'画像サイズ不一致: {image.width()}×{image.height()} / カメラ {width}×{height}')
        self.scene().clear(); self.marks=[]
        self.pix=self.scene().addPixmap(QPixmap.fromImage(image))
        self.scene().setSceneRect(self.pix.boundingRect())
        self.resetTransform(); self.fit()
    def fit(self):
        if self.pix: self.fitInView(self.pix,Qt.AspectRatioMode.KeepAspectRatio)
    def wheelEvent(self,event):
        self.scale(1.2 if event.angleDelta().y()>0 else 1/1.2,
                   1.2 if event.angleDelta().y()>0 else 1/1.2)
    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            self.start=event.position(); self.pan_last=event.position(); self.panning=False
            event.accept(); return
        super().mousePressEvent(event)
    def mouseMoveEvent(self,event):
        if self.start is not None and event.buttons() & Qt.MouseButton.LeftButton:
            if self.panning or (event.position()-self.start).manhattanLength()>=4:
                self.panning=True
                delta=event.position()-self.pan_last
                self.horizontalScrollBar().setValue(self.horizontalScrollBar().value()-round(delta.x()))
                self.verticalScrollBar().setValue(self.verticalScrollBar().value()-round(delta.y()))
                self.pan_last=event.position()
            event.accept(); return
        super().mouseMoveEvent(event)
    def mouseReleaseEvent(self,event):
        if self.pix and self.start is not None and event.button()==Qt.MouseButton.LeftButton:
            if not self.panning and (event.position()-self.start).manhattanLength()<4:
                # Event position is the target center hotspot, mapped to image coordinates.
                xy=self.viewportTransform().inverted()[0].map(event.position())
                if self.pix.boundingRect().contains(xy): self.clicked.emit(xy.x(),xy.y())
        self.start=None; self.pan_last=None; self.panning=False
        super().mouseReleaseEvent(event)
    def markers(self,points,predictions=None):
        for item in self.marks: self.scene().removeItem(item)
        self.marks=[]
        for i,(name,xy) in enumerate(points.items()):
            color=QColor(['#ffcc00','#00ddff','#ff77aa','#77ff88','#c899ff'][i%5])
            # Ignore zoom transform for a readable crosshair and label.
            cross=self.scene().addText('＋'); cross.setDefaultTextColor(color)
            cross.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            cross.setPos(xy[0],xy[1]); cross.setTransformOriginPoint(0,0)
            text=self.scene().addText(name); text.setDefaultTextColor(color)
            text.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            text.setPos(xy[0]+8,xy[1]+8)
            dot=self.scene().addEllipse(-2,-2,4,4,QPen(color),color)
            dot.setPos(*xy); dot.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            self.marks.extend([cross,text,dot])
            if predictions and name in predictions:
                px,py=predictions[name]
                line=self.scene().addLine(xy[0],xy[1],px,py,QPen(QColor('#ff4444')))
                self.marks.append(line)


class PresetDialog(QDialog):
    def __init__(self,preset=None,parent=None):
        super().__init__(parent); self.setWindowTitle('規定マーカースケール設定'); self.resize(650,550)
        layout=QVBoxLayout(self); form=QFormLayout()
        self.name=QLineEdit(); self.family=QComboBox(); self.family.addItems(FAMILIES)
        form.addRow('設定名',self.name); form.addRow('AprilTagファミリー',self.family); layout.addLayout(form)
        layout.addWidget(QLabel('印刷時と同じファミリー・IDを指定。使用しない点のIDは空欄にします。'))
        self.ids=QTableWidget(5,2); self.ids.setHorizontalHeaderLabels(['点','印刷タグID'])
        for r,p in enumerate('ABCDE'):
            item=QTableWidgetItem(p); item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.ids.setItem(r,0,item); self.ids.setItem(r,1,QTableWidgetItem(''))
        layout.addWidget(self.ids)
        layout.addWidget(QLabel('実測したマーカー中心間距離（mm）。未使用の区間は端点・距離を空欄にします。'))
        self.distances=QTableWidget(5,4); self.distances.setHorizontalHeaderLabels(['始点','終点','実寸 mm','用途'])
        for r in range(5):
            for c,v in enumerate(['','','','scale' if r<3 else 'check']): self.distances.setItem(r,c,QTableWidgetItem(v))
        layout.addWidget(self.distances)
        self.message=QLabel(); self.message.setWordWrap(True); layout.addWidget(self.message)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept_checked); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
        if preset:
            self.name.setText(preset.get('name','')); self.family.setCurrentText(preset['family'])
            for r,p in enumerate('ABCDE'):
                if p in preset['markers']: self.ids.item(r,1).setText(str(preset['markers'][p]))
            for r,b in enumerate(preset['bars']):
                for c,v in enumerate([b['a'],b['b'],str(b['length_mm']),b['role']]): self.distances.item(r,c).setText(v)
    def accept_checked(self):
        try:
            ids={p:int(self.ids.item(r,1).text().strip()) for r,p in enumerate('ABCDE') if self.ids.item(r,1).text().strip()}
            bars=[]
            for r in range(5):
                a,b,length,role=[self.distances.item(r,c).text().strip() for c in range(4)]
                if not a and not b and not length: continue
                bars.append({'a':a,'b':b,'length_mm':float(length),'role':role})
            self.preset=validate_preset({'version':1,'type':'apriltag','name':self.name.text().strip(),
                'family':self.family.currentText(),'markers':ids,'bars':bars})
        except (ValueError,TypeError) as e:
            self.message.setText(str(e)); return
        self.accept()


class DetectionWorker(QThread):
    ready=Signal(object); failed=Signal(str); progress=Signal(int,int,str)
    def __init__(self,preset,root,images,parent=None):
        super().__init__(parent); self.preset=preset; self.root=root; self.images=images
    def run(self):
        try:
            result=detect_scale(self.preset,self.root,self.images,
                progress=lambda i,n,name:self.progress.emit(i,n,name))
        except Exception as e: self.failed.emit(str(e))
        else: self.ready.emit(result)


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f'Scale Bridge {VERSION} — 写真から実寸スケールを設定')
        self.resize(1350,950)
        self.model=None; self.observations={}; self.result=None
        self.diagnostics={}; self.diagnostic_failures={}
        self.scale_preset=None; self.auto_worker=None
        self.current_name=None; self.image_valid=False
        root=QWidget(); self.setCentralWidget(root); layout=QVBoxLayout(root)
        self.paths={}
        for key,label,directory in [('model','COLMAP sparseフォルダ',True),
                                    ('images','対応画像フォルダ',True),
                                    ('xml','MetashapeカメラXML（任意）',False)]:
            row=QHBoxLayout(); row.addWidget(QLabel(label)); edit=QLineEdit()
            self.paths[key]=edit; row.addWidget(edit)
            button=QPushButton('選択'); button.clicked.connect(
                lambda _,k=key,d=directory:self.browse(k,d)); row.addWidget(button)
            edit.textChanged.connect(self.invalidate_source if key in ('model','images') else self.invalidate)
            layout.addLayout(row)
        row=QHBoxLayout()
        self.button(row,'読み込み',self.load)
        self.button(row,'作業を保存',self.save_session)
        self.button(row,'作業を再開',self.open_session)
        self.button(row,'全体表示',lambda:self.photo.fit())
        layout.addLayout(row)
        layout.addWidget(QLabel('同じ目盛りを複数写真で指定。ホイール: 拡大縮小 / ドラッグ: 移動 / クリック: 選択点を追加・更新'))
        splitter=QSplitter(); layout.addWidget(splitter,1)
        left=QWidget(); ll=QVBoxLayout(left)
        ll.addWidget(QLabel('打点用写真（指定フォルダにあるCOLMAP登録画像のみ）'))
        self.image_summary=QLabel('画像フォルダを指定して「読み込み」を押してください。')
        self.image_summary.setWordWrap(True); ll.addWidget(self.image_summary)
        self.photo_names=QComboBox(); self.photo_names.currentTextChanged.connect(self.show_photo)
        self.photo_names.setEnabled(False)
        ll.addWidget(self.photo_names)
        nav=QHBoxLayout()
        self.previous_photo=self.button(nav,'前へ',lambda:self.step_photo(-1))
        self.next_photo=self.button(nav,'次へ',lambda:self.step_photo(1))
        self.photo_counter=QLabel('0 / 0'); nav.addWidget(self.photo_counter)
        ll.addLayout(nav)
        self.photo_names.currentIndexChanged.connect(self.update_navigation)
        self.update_navigation()
        self.photo=PhotoView(); self.photo.clicked.connect(self.add_observation); ll.addWidget(self.photo,1)
        self.photo_status=QLabel('未読み込み'); ll.addWidget(self.photo_status); splitter.addWidget(left)
        right=QWidget(); rl=QVBoxLayout(right)
        right_scroll=QScrollArea(); right_scroll.setWidgetResizable(True); right_scroll.setWidget(right)
        splitter.addWidget(right_scroll); splitter.setSizes([850,450])
        row=QHBoxLayout(); row.addWidget(QLabel('打点する点名'))
        self.point=QComboBox(); self.point.addItems(['A','B','C','D','E'])
        row.addWidget(self.point); rl.addLayout(row)
        self.obs_table=QTableWidget(0,5); self.obs_table.setHorizontalHeaderLabels(['点','写真','x','y','再投影誤差 px'])
        self.obs_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.obs_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.obs_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.obs_table.setMaximumHeight(180)
        rl.addWidget(self.obs_table)
        self.button(rl,'選択した打点を削除',self.delete_observation)
        rl.addWidget(QLabel('距離入力（mm）: scale 最大3区間 / check 最大2区間'))
        self.bars=QTableWidget(0,4); self.bars.setHorizontalHeaderLabels(['始点','終点','実寸 mm','用途'])
        self.bars.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.bars.setMaximumHeight(140)
        self.bars.itemChanged.connect(self.invalidate); rl.addWidget(self.bars)
        row=QHBoxLayout(); self.button(row,'距離を追加',self.add_bar); self.button(row,'距離を削除',self.delete_bar)
        rl.addLayout(row); self.add_bar()
        settings=QGroupBox('計算の判定値'); form=QFormLayout(settings)
        self.error=QDoubleSpinBox(); self.error.setRange(.01,100); self.error.setValue(10); self.error.setSuffix(' px')
        self.angle=QDoubleSpinBox(); self.angle.setRange(.01,45); self.angle.setValue(1); self.angle.setSuffix(' °')
        for w in (self.error,self.angle): w.valueChanged.connect(self.invalidate)
        form.addRow('最大再投影誤差',self.error); form.addRow('最小交会角',self.angle); rl.addWidget(settings)
        preset=QGroupBox('規定マーカースケール'); preset_layout=QVBoxLayout(preset)
        preset_row=QHBoxLayout()
        self.button(preset_row,'設定',self.configure_preset)
        self.button(preset_row,'設定を読み込む',self.load_preset)
        self.button(preset_row,'設定を保存',self.save_preset)
        preset_layout.addLayout(preset_row)
        self.preset_status=QLabel('未設定（AprilTagのIDと実測距離を登録）'); self.preset_status.setWordWrap(True)
        preset_layout.addWidget(self.preset_status)
        self.auto_button=self.button(preset_layout,'自動検出 → 距離適用 → 計算',self.detect_and_calculate)
        rl.addWidget(preset)
        self.button(rl,'縮尺を計算',self.calculate)
        self.report=QPlainTextEdit(); self.report.setReadOnly(True); self.report.setMinimumHeight(150); rl.addWidget(self.report,1)
        row=QHBoxLayout(); self.unit=QComboBox(); self.unit.addItems(['m','mm']); row.addWidget(QLabel('COLMAP出力単位')); row.addWidget(self.unit)
        self.button(row,'COLMAPを書き出す',self.colmap_out); rl.addLayout(row)
        self.button(rl,'Metashape XMLを書き出す（m）',self.xml_out)
        layout.addWidget(QLabel('XML再読み込みは実機検証が必要です。新しいチャンクで生成し、別の既知距離を確認してください。'))
    def button(self,layout,text,callback):
        b=QPushButton(text); b.clicked.connect(lambda _:self.guard(callback)); layout.addWidget(b); return b
    def guard(self,callback):
        try: callback()
        except Exception as e: QMessageBox.warning(self,'処理を完了できません',str(e))
    def update_navigation(self,*args):
        i=self.photo_names.currentIndex(); count=self.photo_names.count()
        self.previous_photo.setEnabled(i>0)
        self.next_photo.setEnabled(0<=i<count-1)
        self.photo_counter.setText(f'{i+1 if i>=0 else 0} / {count}')
    def step_photo(self,delta):
        i=self.photo_names.currentIndex()+delta
        if 0<=i<self.photo_names.count(): self.photo_names.setCurrentIndex(i)
    def set_preset(self,preset):
        self.scale_preset=validate_preset(preset)
        label=self.scale_preset.get('name') or '規定スケール'
        self.preset_status.setText(f"{label} / {self.scale_preset['family']} / {len(self.scale_preset['markers'])}点")
        self.invalidate()
    def configure_preset(self):
        dialog=PresetDialog(self.scale_preset,self)
        if dialog.exec()==QDialog.DialogCode.Accepted: self.set_preset(dialog.preset)
    def load_preset(self):
        name=QFileDialog.getOpenFileName(self,'規定スケール設定を読み込む','','JSON (*.json)')[0]
        if name: self.set_preset(json.loads(Path(name).read_text(encoding='utf-8')))
    def save_preset(self):
        if not self.scale_preset: raise ValueError('先に規定マーカースケールを設定してください。')
        name=QFileDialog.getSaveFileName(self,'規定スケール設定を保存','marker_scale.json','JSON (*.json)')[0]
        if name: write_json(name,self.scale_preset)
    def detect_and_calculate(self):
        if not self.model: raise ValueError('COLMAPと画像を読み込んでください。')
        if not self.scale_preset: raise ValueError('先に「設定」で印刷IDと実測中心間距離を登録してください。')
        if self.auto_worker is not None and self.auto_worker.isRunning(): return
        images=[(name,self.model.camera(name).width,self.model.camera(name).height)
                for name in (self.photo_names.itemText(i) for i in range(self.photo_names.count()))]
        if not images: raise ValueError('自動検出対象の画像がありません。')
        self.invalidate()
        self.auto_worker=DetectionWorker(self.scale_preset,self.paths['images'].text(),images,self)
        self.auto_worker.ready.connect(lambda payload:self.guard(lambda:self.apply_detection(payload)))
        self.auto_worker.failed.connect(self.detection_failed)
        self.auto_worker.progress.connect(lambda i,n,name:self.preset_status.setText(f'検出中 {i} / {n}: {name}'))
        self.auto_worker.finished.connect(self.finish_detection)
        self.centralWidget().setEnabled(False)
        self.preset_status.setText('自動検出を開始しています…'); self.auto_worker.start()
    def finish_detection(self):
        worker=self.auto_worker; self.auto_worker=None
        if worker: worker.deleteLater()
        self.centralWidget().setEnabled(True)
    def detection_failed(self,message):
        self.preset_status.setText('自動検出を停止しました。既存の打点・距離は保持しています。')
        self.report.setPlainText(message)
        QMessageBox.warning(self,'自動検出を完了できません',message)
    def apply_detection(self,payload):
        # Replace as one batch: no mixing of previous objects or manual sessions.
        self.observations=payload['observations']
        self.bars.blockSignals(True); self.bars.setRowCount(0)
        for b in payload['bars']:
            r=self.bars.rowCount(); self.bars.insertRow(r)
            for c,v in enumerate([b['a'],b['b'],str(b['length_m']*1000),b['role']]): self.bars.setItem(r,c,QTableWidgetItem(v))
        self.bars.blockSignals(False)
        self.invalidate(); self.refresh_observations(); self.refresh_markers()
        counts=payload['detection']['counts']
        self.preset_status.setText('検出完了: '+', '.join(f'{p}={n}枚' for p,n in counts.items()))
        self.calculate()  # Includes all-marker diagnostics even when thresholds fail.
        self.result['automatic_detection']=payload['detection']
    def closeEvent(self,event):
        if self.auto_worker is not None and self.auto_worker.isRunning():
            self.preset_status.setText('検出処理中です。完了後に閉じてください。'); event.ignore(); return
        super().closeEvent(event)
    def browse(self,key,directory):
        p=QFileDialog.getExistingDirectory(self,'フォルダ選択') if directory else QFileDialog.getOpenFileName(self,'カメラXMLを選択','','XML (*.xml)')[0]
        if p: self.paths[key].setText(p)
    def invalidate(self,*args):
        self.result=None
        self.diagnostics={}; self.diagnostic_failures={}
        if hasattr(self,'obs_table'): self.refresh_observations()
        if hasattr(self,'photo'): self.refresh_markers()
        if hasattr(self,'report'): self.report.setPlainText('入力が変更されました。縮尺を計算してください。')
    def invalidate_source(self,*args):
        self.model=None; self.image_valid=False; self.current_name=None
        if hasattr(self,'photo_names'):
            self.photo_names.blockSignals(True); self.photo_names.clear(); self.photo_names.blockSignals(False)
            self.photo_names.setEnabled(False); self.photo.clear()
            self.update_navigation()
            self.photo_status.setText('入力先が変更されました。「読み込み」を押してください。')
            self.image_summary.setText('未読み込み')
        self.invalidate()
    def load(self, preserve=False):
        candidate=Model(self.paths['model'].text())
        root=Path(self.paths['images'].text()).resolve()
        if not root.is_dir(): raise ValueError('対応画像フォルダを選択してください。')
        available=[]
        for name in sorted(candidate.images):
            path=(root/name).resolve()
            if path.is_relative_to(root) and path.is_file(): available.append(name)
        if not preserve: self.observations={}
        self.model=candidate; self.invalidate()
        self.photo_names.blockSignals(True); self.photo_names.clear()
        self.photo_names.addItems(available); self.photo_names.blockSignals(False)
        self.photo_names.setEnabled(bool(available))
        self.update_navigation()
        self.image_summary.setText(
            f'打点用画像: {len(available)}枚 / COLMAP登録: {len(candidate.images)}台 / '
            f'画像未配置: {len(candidate.images)-len(available)}枚\n'
            '写真一覧だけを絞り込みます。全カメラ情報は計算・出力用に保持します。')
        self.photo.clear(); self.current_name=None; self.image_valid=False
        if not available:
            self.photo_status.setText('対応する画像がありません。補正画像のフォルダ・ファイル名・相対パスを確認してください。')
        self.show_photo(self.photo_names.currentText()); self.refresh_observations()
    def show_photo(self,name):
        self.current_name=None; self.image_valid=False
        self.photo.clear()
        if not self.model or not name: return
        try:
            camera=self.model.camera(name)
            image_root=Path(self.paths['images'].text()).resolve()
            p=(image_root/name).resolve()
            if not p.is_relative_to(image_root): raise ValueError('画像パスが指定フォルダ外です。')
            self.photo.load(p,camera.width,camera.height)
            self.current_name=name; self.image_valid=True
            self.photo_status.setText(f'{camera.width}×{camera.height} px / {camera.model_name} / EXIF自動回転なし')
            self.refresh_markers()
        except Exception as e: self.photo_status.setText(str(e))
    def refresh_markers(self):
        if not self.current_name: return
        points={p:obs[self.current_name] for p,obs in self.observations.items() if self.current_name in obs}
        predictions={}
        if self.diagnostics:
            for p in points:
                if p in self.diagnostics: predictions[p]=self.model.project(self.current_name,np.array(self.diagnostics[p]['xyz']))
        self.photo.markers(points,predictions)
    def add_observation(self,x,y):
        if not self.model or not self.image_valid: return
        p=self.point.currentText().strip()
        if not p: return
        self.observations.setdefault(p,{})[self.current_name]=[x,y]
        self.invalidate(); self.refresh_observations(); self.refresh_markers()
    def refresh_observations(self):
        self.obs_rows=[]
        for p,obs in sorted(self.observations.items()):
            for n,xy in sorted(obs.items()): self.obs_rows.append((p,n,xy))
        self.obs_table.setRowCount(len(self.obs_rows))
        for r,(p,n,xy) in enumerate(self.obs_rows):
            error=self.diagnostics.get(p,{}).get('errors_px',{}).get(n)
            status=f'{error:.3f}' if error is not None else ('計算不可' if p in self.diagnostic_failures else '—')
            for c,v in enumerate([p,n,f'{xy[0]:.3f}',f'{xy[1]:.3f}',status]): self.obs_table.setItem(r,c,QTableWidgetItem(v))
            item=self.obs_table.item(r,4)
            if error is not None and error>self.error.value():
                item.setForeground(QColor('#cc2222')); item.setText(status+' 超過')
            if p in self.diagnostic_failures: item.setToolTip(self.diagnostic_failures[p])
    def delete_observation(self):
        rows=sorted({i.row() for i in self.obs_table.selectedIndexes()},reverse=True)
        for r in rows:
            p,n,_=self.obs_rows[r]; del self.observations[p][n]
            if not self.observations[p]: del self.observations[p]
        self.invalidate(); self.refresh_observations(); self.refresh_markers()
    def add_bar(self):
        if self.bars.rowCount()>=5: raise ValueError('距離入力はscale最大3区間、check最大2区間、合計5区間です。')
        r=self.bars.rowCount(); self.bars.insertRow(r)
        scale_count=sum(self.bars.item(i,3) is not None and self.bars.item(i,3).text().strip()=='scale' for i in range(r))
        for c,v in enumerate(['A','B','100','scale' if scale_count<3 else 'check']): self.bars.setItem(r,c,QTableWidgetItem(v))
        self.invalidate()
    def delete_bar(self):
        rows=sorted({i.row() for i in self.bars.selectedIndexes()},reverse=True)
        for r in rows: self.bars.removeRow(r)
        self.invalidate()
    def distances(self):
        result=[]
        for r in range(self.bars.rowCount()):
            vals=[self.bars.item(r,c).text().strip() if self.bars.item(r,c) else '' for c in range(4)]
            if vals[0] not in 'ABCDE' or vals[1] not in 'ABCDE' or len(vals[0])!=1 or len(vals[1])!=1:
                raise ValueError('距離の始点・終点にはA〜Eを指定してください。')
            result.append({'a':vals[0],'b':vals[1],'length_m':float(vals[2])/1000,'role':vals[3]})
        return result
    def calculate(self):
        if not self.model: raise ValueError('COLMAPと画像を読み込んでください。')
        # Do not retain an earlier successful result after a failed recomputation.
        self.result=None
        self.diagnostics={}; self.diagnostic_failures={}
        try:
            self.result=solve(self.model,self.observations,self.distances(),self.angle.value(),self.error.value())
        except CalculationError as e:
            self.diagnostics=e.points; self.diagnostic_failures=e.failures
            self.report.setPlainText('縮尺は確定していません。打点一覧の再投影誤差を確認してください。\n'+str(e))
            raise
        finally:
            if self.result: self.diagnostics=self.result['points']
            self.refresh_observations(); self.refresh_markers()
        r=self.result; lines=[f"縮尺: {r['meters_per_model_unit']:.12g} m / モデル単位"]
        for p,v in r['points'].items():
            lines.append(f"{p}: {len(v['errors_px'])}枚 / RMS {v['rms_px']:.3f}px / 最大交会角 {v['max_intersection_angle_deg']:.2f}°")
            lines.extend(f'  {n}: {e:.3f}px' for n,e in v['errors_px'].items())
            lines.extend('注意: '+w for w in v['warnings'])
        for b in r['distances']:
            lines.append(f"{b['a']}–{b['b']} [{b.get('role','scale')}]: {b['estimated_m']*1000:.4f} mm / 差 {b['residual_m']*1000:+.4f} mm ({b['relative_error_percent']:+.3f}%)")
        lines.extend('注意: '+w for w in r['warnings']); self.report.setPlainText('\n'.join(lines)); self.refresh_markers()
    def require_result(self):
        if not self.result or not self.model: raise ValueError('現在の入力で縮尺を計算してください。')
    def colmap_out(self):
        self.require_result()
        name=QFileDialog.getSaveFileName(self,'新しい出力フォルダ名を指定（既存不可）','scaled_colmap','')[0]
        if not name:return
        export_colmap(self.model,name,self.result['meters_per_model_unit'],self.unit.currentText())
        write_json(Path(name)/'measurement_report.json',self.result)
        QMessageBox.information(self,'保存完了','縮尺補正済みCOLMAPと計算記録を保存しました。')
    def xml_out(self):
        self.require_result()
        xml=self.paths['xml'].text()
        if not xml:raise ValueError('同じアラインメントから書き出したAgisoft XMLを選択してください。')
        name=QFileDialog.getSaveFileName(self,'新しいXMLを保存','scaled_cameras.xml','XML (*.xml)')[0]
        if not name:return
        report_path=Path(name).with_suffix('.report.json')
        if report_path.exists():raise ValueError('計算記録ファイルが既に存在します。別名にしてください。')
        report=export_metashape(self.model,xml,name,self.result['meters_per_model_unit'])
        write_json(report_path,{**self.result,'metashape_export':report})
        QMessageBox.information(self,'保存完了','XMLを保存しました。Metashapeの新規チャンクに元画像を追加し、Import Camerasで読み込んでください。再アラインメントせず生成し、実寸を検証してください。')
    def save_session(self):
        if not self.model:raise ValueError('モデルを読み込んでください。')
        name=QFileDialog.getSaveFileName(self,'作業を保存','scale_session.json','JSON (*.json)')[0]
        if name:write_json(name,{'version':VERSION,'paths':{k:w.text() for k,w in self.paths.items()},
            'model_fingerprint':self.model.signature,'observations':self.observations,'bars':self.distances(),
            'min_angle_deg':self.angle.value(),'max_error_px':self.error.value(),'scale_preset':self.scale_preset})
    def open_session(self):
        name=QFileDialog.getOpenFileName(self,'作業を再開','','JSON (*.json)')[0]
        if not name:return
        data=json.loads(Path(name).read_text())
        for k,w in self.paths.items():
            value=data['paths'].get(k,'')
            if value and not Path(value).is_absolute():value=str((Path(name).parent/value).resolve())
            w.setText(value)
        self.load(preserve=False)
        if self.model.signature!=data['model_fingerprint']:
            raise ValueError('カメラデータが保存時と異なります。打点は復元しません。')
        self.observations=data['observations']; self.bars.setRowCount(0)
        for b in data['bars']:
            r=self.bars.rowCount(); self.bars.insertRow(r)
            for c,v in enumerate([b['a'],b['b'],str(b['length_m']*1000),b.get('role','scale')]):
                self.bars.setItem(r,c,QTableWidgetItem(v))
        self.angle.setValue(data.get('min_angle_deg',1)); self.error.setValue(data.get('max_error_px',10))
        if data.get('scale_preset'): self.set_preset(data['scale_preset'])
        else:
            self.scale_preset=None; self.preset_status.setText('未設定（AprilTagのIDと実測距離を登録）')
        self.invalidate(); self.refresh_observations(); self.refresh_markers()


def main():
    app=QApplication(sys.argv); app.setStyle('Fusion'); window=Window(); window.show()
    sys.exit(app.exec())

if __name__=='__main__':main()
