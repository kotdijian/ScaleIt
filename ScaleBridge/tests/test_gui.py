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
