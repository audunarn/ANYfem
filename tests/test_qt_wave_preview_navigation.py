import os
import numpy as np
import pytest
if os.environ.get('ANYFEM_RUN_QT_TESTS')!='1':pytest.skip('Qt opt-in',allow_module_level=True)
from PySide6.QtCore import Qt,QPoint
from PySide6.QtTest import QTest
from test_qt_geometry_actions import qapp
from anyfem.ui.qt.wave_preview_plot import WaveResultantPlot


def record(n=201,t0=0.):
    t=t0+np.linspace(0.,20.,n)
    v=np.column_stack([(k+1)*1000.*np.sin(2*np.pi*t/(4.+k)+k) for k in range(6)])
    return t,v


def make(qapp,t=None,v=None,interval=None):
    plot=WaveResultantPlot();plot.resize(760,480);plot.show()
    if t is None:t,v=record()
    plot.set_data(t,v,interval);qapp.processEvents();return plot


def pt(plot,time,frac=.5,index=0):
    rect=plot.plot_rects()[index];return QPoint(round(plot._x(time,rect)),round(rect.top()+frac*rect.height()))


def drag(plot,a,b):
    QTest.mousePress(plot,Qt.LeftButton,Qt.NoModifier,a)
    QTest.mouseMove(plot,b,5);QTest.mouseRelease(plot,Qt.LeftButton,Qt.NoModifier,b)


def test_extrema_for_all_six_graphs_use_raw_samples_and_earliest_tie(qapp):
    t,v=record()
    plot=make(qapp,t,v);ext=plot.visible_extrema();assert len(ext)==6
    for k,(lo,tlo,hi,thi) in enumerate(ext):
        col=v[:,k]/1000.
        assert lo==col.min() and hi==col.max()
        assert tlo==t[int(np.argmin(col))] and thi==t[int(np.argmax(col))]
    assert len({e[3] for e in ext})>1


def test_long_record_single_sample_spike_is_marked(qapp):
    t=np.linspace(0.,100.,200001);v=np.zeros((len(t),6));v[123457,2]=7000.;v[54321,5]=-3000.
    plot=make(qapp,t,v);ext=plot.visible_extrema()
    assert ext[2][2]==7. and ext[2][3]==t[123457]
    assert ext[5][0]==-3. and ext[5][1]==t[54321]


def spy_envelopes(monkeypatch):
    import anyfem.ui.qt.wave_preview_plot as module
    calls=[];real=module.pixel_extrema
    def spy(times,values,pixels):
        calls.append((np.array(times),np.array(values),pixels));return real(times,values,pixels)
    monkeypatch.setattr(module,'pixel_extrema',spy);return calls


def long_record(spike_time):
    t=np.linspace(0.,10.,20001);v=np.zeros((len(t),6));v[int(np.argmin(abs(t-spike_time))),:]=9.0e6;return t,v


def test_long_record_envelopes_exclude_spike_just_outside_viewport(qapp,monkeypatch):
    t,v=long_record(2.9995);plot=make(qapp,t,v,(3.,7.));calls=spy_envelopes(monkeypatch)
    plot.fit_selected();qapp.processEvents();plot.grab()
    a,b=plot.view_interval;assert a>=3.-1e-9 and a<=3.+1e-9
    assert len(calls)>=6 and all(len(c[0])>2*c[2] for c in calls)  # repeat paints may add calls
    for times,values,_ in calls:
        assert times.min()>=a and times.max()<=b and not values.any()
    assert all(e[2]==0. and e[0]==0. for e in plot.visible_extrema())


def test_long_record_envelopes_keep_spike_inside_viewport(qapp,monkeypatch):
    t,v=long_record(5.);plot=make(qapp,t,v,(3.,7.));calls=spy_envelopes(monkeypatch)
    plot.fit_selected();qapp.processEvents();plot.grab()
    assert len(calls)>=6 and all(c[1].max()==9.0e3 for c in calls)
    assert all(e[2]==9.0e3 and e[3]==5. for e in plot.visible_extrema())


def test_flat_tied_and_positive_only_signals(qapp):
    t=np.arange(0.,10.);v=np.zeros((10,6));v[:,0]=5000.;v[:,1]=1000.*np.array([1,3,3,1,3,1,1,1,1,1]);v[:,2]=np.arange(10)*1000.+1000
    plot=make(qapp,t,v);ext=plot.visible_extrema()
    assert ext[0]==(5.,0.,5.,0.)
    assert ext[1][2]==3. and ext[1][3]==1. and ext[1][1]==0.
    assert ext[3]==(0.,0.,0.,0.)
    assert ext[2][0]==1. and ext[2][2]==10.
    assert all(b>0 for b in plot.y_bounds())
    plot.grab()


def test_late_absolute_times_view_and_labels(qapp):
    t,v=record(t0=3600.);plot=make(qapp,t,v)
    assert plot.view_interval==(3600.,3620.)
    assert plot._x(3610.,plot.plot_rects()[0])==pytest.approx(plot.plot_rects()[0].center().x())
    drag(plot,pt(plot,3605.),pt(plot,3615.));lo,hi=plot.view_interval
    assert lo==pytest.approx(3605.,abs=.2) and hi==pytest.approx(3615.,abs=.2)
    assert all(lo-1e-9<=e[1]<=hi+1e-9 and lo-1e-9<=e[3]<=hi+1e-9 for e in plot.visible_extrema())


def test_zoom_in_updates_all_plots_extrema_and_y_ranges_without_selection_change(qapp):
    t,v=record();v[30,:]=50000.  # isolated spike (t=3) outside the later zoom range dominates the full-record range
    plot=make(qapp,t,v,(2.,18.));seen=[];plot.selectionChanged.connect(lambda a,b:seen.append((a,b)))
    interval=plot.interval;times=plot.times.copy();values=plot.values.copy();before=plot.y_bounds()
    drag(plot,pt(plot,5.,.5,4),pt(plot,10.,.5,4))
    lo,hi=plot.view_interval
    assert lo==pytest.approx(5.,abs=.2) and hi==pytest.approx(10.,abs=.2)
    mask=(t>=lo)&(t<=hi)
    for k,b in enumerate(plot.y_bounds()):assert b==pytest.approx(np.abs(v[mask,k]/1000.).max()*1.08)
    assert plot.y_bounds()!=before
    for k,(mn,tmn,mx,tmx) in enumerate(plot.visible_extrema()):
        col=v[mask,k]/1000.;assert mn==col.min() and mx==col.max()
    assert seen==[] and plot.interval==interval
    assert np.array_equal(plot.times,times) and np.array_equal(plot.values,values)
    plot.grab()


def test_reverse_drag_zooms_out_clamped_to_record(qapp):
    plot=make(qapp);drag(plot,pt(plot,5.),pt(plot,10.));a,b=plot.view_interval
    rect=plot.plot_rects()[0];x0=plot._x(b,rect)-1;x1=x0-rect.width()/2
    QTest.mousePress(plot,Qt.LeftButton,Qt.NoModifier,QPoint(round(x0),round(rect.center().y())))
    QTest.mouseMove(plot,QPoint(round(x1),round(rect.center().y())),5)
    QTest.mouseRelease(plot,Qt.LeftButton,Qt.NoModifier,QPoint(round(x1),round(rect.center().y())))
    c,d=plot.view_interval
    assert d-c==pytest.approx(2*(b-a),rel=.05) and c<=a and d>=b-1e-9
    for _ in range(4):
        drag(plot,pt(plot,c+(d-c)*.9),QPoint(round(rect.left()+2),round(rect.center().y())))
    assert plot.view_interval[0]>=0. and plot.view_interval[1]<=20.


def test_all_plots_share_viewport_through_each_graph(qapp):
    plot=make(qapp)
    for index in range(6):
        plot.fit_all();drag(plot,pt(plot,4.,.5,index),pt(plot,8.,.5,index))
        assert plot.view_interval[0]==pytest.approx(4.,abs=.2)
        for rect in plot.plot_rects():assert plot._x(plot.view_interval[0],rect)==pytest.approx(rect.left())


def test_fit_buttons_do_not_change_selection(qapp):
    plot=make(qapp,interval=(6.,12.));seen=[];plot.selectionChanged.connect(lambda a,b:seen.append(1))
    fit_sel=plot.findChild(type(plot.fit_all_button),'WavePlotFitSelected');fit_all=plot.findChild(type(plot.fit_all_button),'WavePlotFitAll')
    assert fit_sel.isEnabled() and fit_all.isEnabled() and fit_sel.toolTip() and fit_all.toolTip()
    interval=plot.interval
    QTest.mouseClick(fit_sel,Qt.LeftButton);assert plot.view_interval==interval
    drag(plot,pt(plot,7.),pt(plot,9.));assert plot.view_interval!=interval
    QTest.mouseClick(fit_all,Qt.LeftButton);assert plot.view_interval==(0.,20.)
    QTest.mouseClick(fit_sel,Qt.LeftButton);assert plot.view_interval==interval
    assert seen==[] and plot.interval==interval


def test_handle_drag_wins_after_zoom_and_snaps_to_original_samples(qapp):
    t,v=record();plot=make(qapp,t,v,(6.,12.));drag(plot,pt(plot,4.),pt(plot,14.))
    seen=[];plot.selectionChanged.connect(lambda a,b:seen.append((a,b)));view=plot.view_interval
    rect=plot.plot_rects()[3];start=pt(plot,6.,.5,3)+QPoint(4,0)
    drag(plot,start,pt(plot,8.04,.5,3))
    assert plot.view_interval==view and seen
    assert plot.interval[0] in t and plot.interval[0]==pytest.approx(8.,abs=.15) and plot.interval[1]==12.
    assert seen[-1]==plot.interval


def test_hidden_endpoint_cannot_steal_body_drag_and_tiny_or_outside_gestures_are_noops(qapp):
    plot=make(qapp,interval=(1.,19.));seen=[];plot.selectionChanged.connect(lambda a,b:seen.append(1))
    drag(plot,pt(plot,8.),pt(plot,12.));view=plot.view_interval;interval=plot.interval
    drag(plot,pt(plot,9.),pt(plot,9.)+QPoint(2,0));assert plot.view_interval==view
    drag(plot,pt(plot,9.),pt(plot,9.)+QPoint(-3,0));assert plot.view_interval==view
    rect=plot.plot_rects()[0];above=QPoint(round(rect.center().x()),round(rect.top()-30))
    drag(plot,above,above+QPoint(80,0));assert plot.view_interval==view
    drag(plot,QPoint(2,2),QPoint(300,2));assert plot.view_interval==view
    assert seen==[] and plot.interval==interval
    drag(plot,pt(plot,9.),pt(plot,11.));assert plot.view_interval[1]-plot.view_interval[0]<view[1]-view[0]


def test_minimum_two_samples_visible(qapp):
    t,v=record(n=21);plot=make(qapp,t,v)
    drag(plot,pt(plot,10.),pt(plot,10.)+QPoint(6,0))
    a,b=plot.view_interval;assert ((t>=a)&(t<=b)).sum()>=2


def test_record_replacement_resets_view_and_old_style_clear_disables_buttons(qapp):
    plot=make(qapp);drag(plot,pt(plot,5.),pt(plot,10.));assert plot.view_interval!=(0.,20.)
    t,v=record(t0=100.);plot.set_data(t,v);assert plot.view_interval==(100.,120.)
    plot.set_interval(105.,110.);assert plot.view_interval==(100.,120.)
    plot.times=None;plot.values=None;plot.interval=None;plot.update();qapp.processEvents()
    assert plot.view_interval is None and plot.visible_extrema()==[] and plot.y_bounds()==[]
    assert not plot.fit_all_button.isEnabled() and not plot.fit_selected_button.isEnabled()
    plot.fit_all();plot.fit_selected();drag(plot,QPoint(100,200),QPoint(300,200));plot.grab()
    plot.set_data(*record());assert plot.fit_all_button.isEnabled()


def test_minimum_size_paints(qapp):
    plot=make(qapp);plot.resize(620,440);qapp.processEvents()
    assert not plot.grab().isNull()
    rects=plot.plot_rects();assert all(r.height()>40 and r.bottom()<plot.height() for r in rects)
    assert plot.fit_selected_button.geometry().bottom()<rects[0].top()-20
