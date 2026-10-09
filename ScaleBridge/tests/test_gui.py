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


def test_target_center_coordinates_and_drag(tmp_path):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QMouseEvent
    app=QApplication.instance() or QApplication([])
    data=create_demo(tmp_path/'demo');w=Window();w.show();app.processEvents()
    for k,v in data['paths'].items():w.paths[k].setText(v)
    w.load();app.processEvents();view=w.photo
    view.scale(4,4)
    point=view.mapFromScene(QPointF(640,480))
    expected=view.mapToScene(point)
    assert view.viewport().cursor().hotSpot().x()==16
    assert view.viewport().cursor().hotSpot().y()==16
    QTest.mouseClick(view.viewport(),Qt.MouseButton.LeftButton,pos=point)
    np.testing.assert_allclose(w.observations['A'][w.current_name],[expected.x(),expected.y()],atol=1e-9)
    assert view.viewport().cursor().shape()==Qt.CursorShape.BitmapCursor
    fractional=QPointF(point)+QPointF(.25,.75)
    for kind,button_state in [(QEvent.Type.MouseButtonPress,Qt.MouseButton.LeftButton),
                              (QEvent.Type.MouseButtonRelease,Qt.MouseButton.NoButton)]:
        event=QMouseEvent(kind,fractional,view.viewport().mapToGlobal(fractional.toPoint()),
            Qt.MouseButton.LeftButton,button_state,Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(view.viewport(),event)
    exact=view.viewportTransform().inverted()[0].map(fractional)
    np.testing.assert_allclose(w.observations['A'][w.current_name],[exact.x(),exact.y()],atol=1e-9)
    w.observations={};x0=view.horizontalScrollBar().value()
    QTest.mousePress(view.viewport(),Qt.MouseButton.LeftButton,pos=point)
    end=QPointF(point)+QPointF(30,20)
    event=QMouseEvent(QEvent.Type.MouseMove,end,view.viewport().mapToGlobal(end.toPoint()),
        Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(view.viewport(),event)
    assert view.panning and view.horizontalScrollBar().value()!=x0
    assert view.viewport().cursor().shape()==Qt.CursorShape.BitmapCursor
    QTest.mouseRelease(view.viewport(),Qt.MouseButton.LeftButton,pos=end.toPoint())
    assert not w.observations
    assert view.viewport().cursor().shape()==Qt.CursorShape.BitmapCursor
    w.close()


def test_five_markers_and_distance_limits(tmp_path):
    import pytest
    app=QApplication.instance() or QApplication([])
    w=Window()
    assert [w.point.itemText(i) for i in range(w.point.count())]==list('ABCDE')
    for _ in range(4): w.add_bar()
    assert [w.bars.item(i,3).text() for i in range(5)]==['scale']*3+['check']*2
    with pytest.raises(ValueError,match='合計5'): w.add_bar()
    data=create_demo(tmp_path/'demo')
    for k,v in data['paths'].items(): w.paths[k].setText(v)
    w.load()
    positions={'A':[0,0,5],'B':[1,0,5],'C':[0,1,5],'D':[1,1,5],'E':[0,0,6]}
    w.observations={p:{n:w.model.project(n,np.array(x)).tolist() for n in w.model.images} for p,x in positions.items()}
    for i,(a,b) in enumerate([('A','B'),('A','C'),('A','E'),('B','D'),('C','D')]):
        w.bars.setItem(i,0,QTableWidgetItem(a)); w.bars.setItem(i,1,QTableWidgetItem(b))
    w.calculate()
    assert set(w.result['points'])==set('ABCDE')
    assert w.result['meters_per_model_unit']==pytest.approx(.1)
    w.bars.setItem(4,3,QTableWidgetItem('scale'))
    with pytest.raises(ValueError,match='Maximum 3'): w.calculate()
    assert w.result is None
    w.bars.setItem(4,3,QTableWidgetItem('check'))
    w.bars.setItem(0,3,QTableWidgetItem('check'))
    with pytest.raises(ValueError,match='Maximum 2'): w.calculate()
    w.close()


def test_reprojection_failures_show_every_marker_and_invalidate(tmp_path):
    import pytest
    from core import CalculationError
    app=QApplication.instance() or QApplication([])
    data=create_demo(tmp_path/'demo'); w=Window()
    for k,v in data['paths'].items(): w.paths[k].setText(v)
    w.load(); w.observations=data['observations']; w.calculate()
    name=sorted(w.model.images)[0]
    w.observations['A'][name][0]+=20
    w.observations['B'][name][1]+=20
    with pytest.raises(CalculationError) as exc: w.calculate()
    assert 'A' in exc.value.failures and 'B' in exc.value.failures
    assert w.result is None and set(w.diagnostics)==set(w.observations)
    for r,(p,n,_) in enumerate(w.obs_rows):
        text=w.obs_table.item(r,4).text()
        assert text!='—' and text!='計算不可'
        if p in ['A','B'] and n==name: assert '超過' in text
    with pytest.raises(ValueError): w.require_result()
    w.error.setValue(100)
    assert not w.diagnostics and all(w.obs_table.item(r,4).text()=='—' for r in range(w.obs_table.rowCount()))
    w.calculate(); assert w.result is not None
    w.observations['C']={name:w.observations['C'][name]}
    with pytest.raises(CalculationError): w.calculate()
    assert any(w.obs_table.item(r,4).text()=='計算不可' for r,(p,n,_) in enumerate(w.obs_rows) if p=='C')
    w.close()


def test_navigation_and_new_default(tmp_path):
    app=QApplication.instance() or QApplication([])
    w=Window()
    assert w.error.value()==10
    assert not w.previous_photo.isEnabled() and not w.next_photo.isEnabled()
    data=create_demo(tmp_path/'demo')
    for k,v in data['paths'].items(): w.paths[k].setText(v)
    w.load()
    assert w.photo_counter.text()=='1 / 4' and not w.previous_photo.isEnabled()
    w.point.setCurrentText('E'); w.add_observation(800,600)
    w.next_photo.click()
    assert w.photo_names.currentIndex()==1 and w.photo_counter.text()=='2 / 4'
    assert len(w.observations['E'])==1
    w.previous_photo.click(); assert w.photo_names.currentIndex()==0
    w.photo_names.setCurrentIndex(3)
    assert not w.next_photo.isEnabled()
    w.step_photo(1); assert w.photo_names.currentIndex()==3
    w.paths['images'].setText(str(tmp_path/'other'))
    assert w.photo_counter.text()=='0 / 0' and not w.previous_photo.isEnabled() and not w.next_photo.isEnabled()
    w.close()


def test_automatic_detection_to_calculation(tmp_path):
    from test_markers import preset,tag_images
    from PySide6.QtWidgets import QMessageBox
    from core import write_json
    import time
    import pytest
    app=QApplication.instance() or QApplication([])
    data=create_demo(tmp_path/'demo'); w=Window()
    for k,v in data['paths'].items(): w.paths[k].setText(v)
    w.load(); tag_images(w.model,tmp_path/'tags')
    w.paths['images'].setText(str(tmp_path/'tags')); w.load()
    w.set_preset(preset()); w.detect_and_calculate()
    assert not w.centralWidget().isEnabled()
    deadline=time.monotonic()+15
    while w.auto_worker is not None and time.monotonic()<deadline:
        app.processEvents(); QTest.qWait(10)
    assert w.auto_worker is None and w.centralWidget().isEnabled()
    assert w.result['meters_per_model_unit']==pytest.approx(.1,rel=.005)
    assert w.bars.rowCount()==2 and w.bars.item(1,3).text()=='check'
    assert w.result['automatic_detection']['counts']=={'A':4,'B':4,'C':4}
    assert all(w.obs_table.item(r,4).text()!='—' for r in range(w.obs_table.rowCount()))
    w.close()


def test_failed_auto_scan_keeps_existing_observations(tmp_path,monkeypatch):
    from test_markers import preset
    from PySide6.QtWidgets import QMessageBox
    from pathlib import Path
    import time
    app=QApplication.instance() or QApplication([])
    data=create_demo(tmp_path/'demo'); w=Window()
    for k,v in data['paths'].items(): w.paths[k].setText(v)
    w.load(); w.observations=data['observations']; w.calculate()
    w.set_preset(preset())
    old=json.loads(json.dumps(w.observations)); old_distances=w.distances()
    (Path(data['paths']['images'])/w.photo_names.itemText(0)).write_bytes(b'broken')
    monkeypatch.setattr(QMessageBox,'warning',lambda *args:None)
    w.detect_and_calculate()
    deadline=time.monotonic()+10
    while w.auto_worker is not None and time.monotonic()<deadline:
        app.processEvents(); QTest.qWait(10)
    assert w.auto_worker is None and w.centralWidget().isEnabled()
    assert w.observations==old and w.distances()==old_distances and w.result is None
    assert '読み込めません' in w.report.toPlainText()
    w.close()


def test_preset_dialog_and_saved_threshold(tmp_path,monkeypatch):
    from gui import PresetDialog
    from test_markers import preset
    from PySide6.QtWidgets import QFileDialog
    from core import write_json
    app=QApplication.instance() or QApplication([])
    dialog=PresetDialog(preset()); dialog.accept_checked()
    assert dialog.preset==preset()
    data=create_demo(tmp_path/'demo'); data['max_error_px']=2
    data['scale_preset']=preset(); path=tmp_path/'session.json'; write_json(path,data)
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *args:(str(path),'JSON'))
    w=Window(); w.open_session()
    assert w.error.value()==2 and w.scale_preset==preset()
    del data['max_error_px']; write_json(path,data); w.open_session()
    assert w.error.value()==10
    w.close()
