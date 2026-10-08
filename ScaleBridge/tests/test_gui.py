import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json
import numpy as np
from PySide6.QtCore import QPointF,Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QTableWidgetItem
from gui import Window
from make_demo import create_demo


def test_actual_clicks_and_invalidation(tmp_path):
    app=QApplication.instance() or QApplication([])
    data=create_demo(tmp_path/'demo'); w=Window(); w.show(); app.processEvents()
    for k,v in data['paths'].items():w.paths[k].setText(v)
    w.load(); app.processEvents()
    for n in sorted(w.model.images):
        w.photo_names.setCurrentText(n); app.processEvents()
        w.photo.scale(2,2)
        for p in ['A','B']:
            w.point.setCurrentText(p)
            xy=data['observations'][p][n]
            viewport=w.photo.mapFromScene(QPointF(*xy))
            QTest.mouseClick(w.photo.viewport(),Qt.MouseButton.LeftButton,pos=viewport)
    assert len(w.observations['A'])==4 and len(w.observations['B'])==4
    w.calculate()
    assert w.result['meters_per_model_unit']==__import__('pytest').approx(.1,rel=.015)
    assert '縮尺' in w.report.toPlainText()
    w.bars.setItem(0,2,QTableWidgetItem('101'))
    assert w.result is None
    w.calculate(); w.delete_bar()  # No selected rows: still invalidates.
    assert w.result is None
    w.paths['model'].setText(str(tmp_path/'other'))
    assert w.model is None and w.image_valid is False
    w.close()


def test_portable_session_restore(tmp_path,monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from core import write_json
    app=QApplication.instance() or QApplication([])
    folder=tmp_path/'demo'; data=create_demo(folder)
    data['paths']={'model':'sparse','images':'images','xml':'original_cameras.xml'}
    write_json(folder/'portable.json',data)
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *args:(str(folder/'portable.json'),'JSON'))
    w=Window(); w.open_session(); w.calculate()
    assert w.result['meters_per_model_unit']==__import__('pytest').approx(.1)
    w.close()
