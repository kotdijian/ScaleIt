from __future__ import annotations
import json
import sys
from pathlib import Path
import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImageReader, QPixmap, QColor, QPen, QPainter
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QPushButton, QLabel, QLineEdit, QComboBox, QDoubleSpinBox,
    QFileDialog, QMessageBox, QGraphicsView, QGraphicsScene, QGraphicsItem,
    QTableWidget, QTableWidgetItem, QHeaderView, QSplitter, QPlainTextEdit,
    QFormLayout, QGroupBox)
from core import Model, solve, export_colmap, export_metashape, write_json, VERSION


class PhotoView(QGraphicsView):
    clicked=Signal(float,float)
    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
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
                # Event position is the arrow cursor hotspot (tip), mapped to image coordinates.
                xy=self.viewportTransform().inverted()[0].map(event.position())
                if self.pix.boundingRect().contains(xy): self.clicked.emit(xy.x(),xy.y())
        self.start=None; self.pan_last=None; self.panning=False
        super().mouseReleaseEvent(event)
    def markers(self,points,predictions=None):
        for item in self.marks: self.scene().removeItem(item)
        self.marks=[]
        for i,(name,xy) in enumerate(points.items()):
            color=QColor(['#ffcc00','#00ddff','#ff77aa','#77ff88'][i%4])
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


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f'Scale Bridge {VERSION} — 写真から実寸スケールを設定')
        self.resize(1350,950)
        self.model=None; self.observations={}; self.result=None
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
        self.photo=PhotoView(); self.photo.clicked.connect(self.add_observation); ll.addWidget(self.photo,1)
        self.photo_status=QLabel('未読み込み'); ll.addWidget(self.photo_status); splitter.addWidget(left)
        right=QWidget(); rl=QVBoxLayout(right); splitter.addWidget(right); splitter.setSizes([850,450])
        row=QHBoxLayout(); row.addWidget(QLabel('打点する点名'))
        self.point=QComboBox(); self.point.setEditable(True); self.point.addItems(['A','B','C','D'])
        row.addWidget(self.point); rl.addLayout(row)
        self.obs_table=QTableWidget(0,4); self.obs_table.setHorizontalHeaderLabels(['点','写真','x','y'])
        self.obs_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.obs_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.obs_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.obs_table.setMaximumHeight(180)
        rl.addWidget(self.obs_table)
        self.button(rl,'選択した打点を削除',self.delete_observation)
        rl.addWidget(QLabel('距離入力（mm）: scale=縮尺決定 / check=検証のみ'))
        self.bars=QTableWidget(0,4); self.bars.setHorizontalHeaderLabels(['始点','終点','実寸 mm','用途'])
        self.bars.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.bars.setMaximumHeight(140)
        self.bars.itemChanged.connect(self.invalidate); rl.addWidget(self.bars)
        row=QHBoxLayout(); self.button(row,'距離を追加',self.add_bar); self.button(row,'距離を削除',self.delete_bar)
        rl.addLayout(row); self.add_bar()
        settings=QGroupBox('計算の判定値'); form=QFormLayout(settings)
        self.error=QDoubleSpinBox(); self.error.setRange(.01,100); self.error.setValue(2); self.error.setSuffix(' px')
        self.angle=QDoubleSpinBox(); self.angle.setRange(.01,45); self.angle.setValue(1); self.angle.setSuffix(' °')
        for w in (self.error,self.angle): w.valueChanged.connect(self.invalidate)
        form.addRow('最大再投影誤差',self.error); form.addRow('最小交会角',self.angle); rl.addWidget(settings)
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
    def browse(self,key,directory):
        p=QFileDialog.getExistingDirectory(self,'フォルダ選択') if directory else QFileDialog.getOpenFileName(self,'カメラXMLを選択','','XML (*.xml)')[0]
        if p: self.paths[key].setText(p)
    def invalidate(self,*args):
        self.result=None
        if hasattr(self,'report'): self.report.setPlainText('入力が変更されました。縮尺を計算してください。')
    def invalidate_source(self,*args):
        self.model=None; self.image_valid=False; self.current_name=None
        if hasattr(self,'photo_names'):
            self.photo_names.blockSignals(True); self.photo_names.clear(); self.photo_names.blockSignals(False)
            self.photo_names.setEnabled(False); self.photo.clear()
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
        if self.result:
            for p in points:
                if p in self.result['points']: predictions[p]=self.model.project(self.current_name,np.array(self.result['points'][p]['xyz']))
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
            for c,v in enumerate([p,n,f'{xy[0]:.3f}',f'{xy[1]:.3f}']): self.obs_table.setItem(r,c,QTableWidgetItem(v))
    def delete_observation(self):
        rows=sorted({i.row() for i in self.obs_table.selectedIndexes()},reverse=True)
        for r in rows:
            p,n,_=self.obs_rows[r]; del self.observations[p][n]
        self.invalidate(); self.refresh_observations(); self.refresh_markers()
    def add_bar(self):
        r=self.bars.rowCount(); self.bars.insertRow(r)
        for c,v in enumerate(['A','B','100','scale']): self.bars.setItem(r,c,QTableWidgetItem(v))
        self.invalidate()
    def delete_bar(self):
        rows=sorted({i.row() for i in self.bars.selectedIndexes()},reverse=True)
        for r in rows: self.bars.removeRow(r)
        self.invalidate()
    def distances(self):
        result=[]
        for r in range(self.bars.rowCount()):
            vals=[self.bars.item(r,c).text().strip() if self.bars.item(r,c) else '' for c in range(4)]
            result.append({'a':vals[0],'b':vals[1],'length_m':float(vals[2])/1000,'role':vals[3]})
        return result
    def calculate(self):
        if not self.model: raise ValueError('COLMAPと画像を読み込んでください。')
        # Do not retain an earlier successful result after a failed recomputation.
        self.result=None
        self.result=solve(self.model,self.observations,self.distances(),self.angle.value(),self.error.value())
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
            'min_angle_deg':self.angle.value(),'max_error_px':self.error.value()})
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
        self.angle.setValue(data.get('min_angle_deg',1)); self.error.setValue(data.get('max_error_px',2))
        self.invalidate(); self.refresh_observations(); self.refresh_markers()


def main():
    app=QApplication(sys.argv); app.setStyle('Fusion'); window=Window(); window.show()
    sys.exit(app.exec())

if __name__=='__main__':main()
