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


def test_image_subset_keeps_all_cameras(tmp_path):
    from pathlib import Path
    import shutil
    from core import Model, export_colmap
    app=QApplication.instance() or QApplication([])
    data=create_demo(tmp_path/'demo'); folder=tmp_path/'selected'; folder.mkdir()
    names=sorted(data['observations']['A'])[:2]
    for name in names:shutil.copyfile(Path(data['paths']['images'])/name,folder/name)
    (folder/'not_registered.jpg').write_bytes(b'unrelated')
    w=Window()
    w.paths['model'].setText(data['paths']['model']);w.paths['images'].setText(str(folder))
    w.load()
    assert [w.photo_names.itemText(i) for i in range(w.photo_names.count())]==names
    assert len(w.model.images)==4 and w.image_valid
    assert '画像未配置: 2枚' in w.image_summary.text()
    w.observations={p:{n:xy for n,xy in obs.items() if n in names} for p,obs in data['observations'].items()}
    w.calculate()
    assert w.result['meters_per_model_unit']==__import__('pytest').approx(.1)
    export_colmap(w.model,tmp_path/'scaled',w.result['meters_per_model_unit'],'m')
    assert len(Model(tmp_path/'scaled').images)==4
    w.close()


def test_empty_folder_and_source_change_clear_view(tmp_path):
    from pathlib import Path
    app=QApplication.instance() or QApplication([])
    data=create_demo(tmp_path/'demo');folder=tmp_path/'empty';folder.mkdir()
    w=Window()
    for k,v in data['paths'].items():w.paths[k].setText(v)
    w.load();assert w.photo.pix is not None
    w.paths['images'].setText(str(folder))
    assert w.photo.pix is None and w.photo_names.count()==0 and not w.image_valid
    w.load()
    assert w.photo_names.count()==0 and not w.photo_names.isEnabled()
    assert len(w.model.images)==4
    assert '対応する画像がありません' in w.photo_status.text()
    w.close()


def test_failed_image_read_clears_old_photo(tmp_path):
    from pathlib import Path
    app=QApplication.instance() or QApplication([])
    data=create_demo(tmp_path/'demo');w=Window()
    for k,v in data['paths'].items():w.paths[k].setText(v)
    w.load();assert w.photo.pix is not None
    second=w.photo_names.itemText(1)
    (Path(data['paths']['images'])/second).write_bytes(b'broken image')
    w.photo_names.setCurrentText(second)
    assert not w.image_valid and w.current_name is None and w.photo.pix is None
    assert '画像を読み込めません' in w.photo_status.text()
    w.close()
