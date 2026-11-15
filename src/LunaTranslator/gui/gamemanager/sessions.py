from qtsymbols import *
from datetime import datetime
import gobject
from myutils.config import _TR, savehook_new_data, globalconfig
from myutils.somedatabase import SESSION_MIN, formatduration, sessioncpm
from gui.dynalang import LTableView, LStandardItemModel
from gui.specialwidget import chartwidget

# 图表显示最近多少次会话（None=全部）
CHART_COUNTS = [10, 30, None]


class cpmchart(chartwidget):
    """每次会话的 CPM 折线图（不跟随热力图/折线切换，始终是折线）"""

    def __init__(self):
        super().__init__(timechart=False)
        self.xtext = lambda x: datetime.fromtimestamp(x).strftime("%m-%d") if x else ""
        self.ytext = lambda y: "{:.0f}".format(y)

    @property
    def usehelper(self):
        return self._helper2


def _item(text, right=False):
    item = QStandardItem(text)
    item.setEditable(False)
    if right:
        item.setTextAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
    return item


def _weightedcpm(sessions):
    seconds = sum(_[3] - _[2] for _ in sessions)
    if seconds <= 0:
        return None
    return sum(_[4] for _ in sessions) / (seconds / 60)


def _counted(sessions):
    # 参与统计/图表的会话：没有被手动排除，且不短于 SESSION_MIN
    return [_ for _ in sessions if not _[7] and _[3] - _[2] >= SESSION_MIN]


SESSIONIDROLE = Qt.ItemDataRole.UserRole + 1


class sessionsview(QWidget):
    """阅读会话：每次会话的时长、字数、CPM（从第一行文本到游戏关闭）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.summary = QLabel(self)
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.count = QComboBox(self)
        self.count.addItems(
            [
                "{} {}".format(_TR("最近"), n) if n else _TR("全部")
                for n in CHART_COUNTS
            ]
        )
        idx = globalconfig.get("sessionchartcount", 1)
        self.count.setCurrentIndex(idx if 0 <= idx < len(CHART_COUNTS) else 1)
        self.count.currentIndexChanged.connect(self.__countchanged)
        top = QHBoxLayout()
        top.addWidget(self.summary, 1)
        top.addWidget(self.count)
        lay.addLayout(top)
        self.chart = cpmchart()
        self.chart.setMinimumHeight(160)
        self.model = LStandardItemModel(self)
        self.table = LTableView(self)
        self.table.setModel(self.model)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.__showmenu)
        self.table.verticalHeader().hide()
        self.table.setMinimumHeight(120)
        # 图表和表格之间可拖动调整高度
        split = QSplitter(Qt.Orientation.Vertical, self)
        split.addWidget(self.chart)
        split.addWidget(self.table)
        split.setChildrenCollapsible(False)
        split.setSizes([260, 300])
        lay.addWidget(split, 1)
        self.__key = None
        self.__gameuid = None
        self.__sessions = []

    def __countchanged(self, idx):
        globalconfig["sessionchartcount"] = idx
        self.__fillchart(self.__sessions)

    def __showmenu(self, pos):
        rows = {i.row() for i in self.table.selectionModel().selectedRows()}
        clicked = self.table.indexAt(pos)
        if clicked.isValid() and clicked.row() not in rows:
            # 右键点在未选中的行上：只操作这一行
            self.table.selectRow(clicked.row())
            rows = {clicked.row()}
        if not rows:
            return
        byid = {_[0]: _ for _ in self.__sessions}
        ids = []
        for row in rows:
            _id = self.model.item(row, 0).data(SESSIONIDROLE)
            if _id in byid:
                ids.append(_id)
        if not ids:
            return
        menu = QMenu(self)
        anyincluded = any(not byid[_][7] for _ in ids)
        anyexcluded = any(byid[_][7] for _ in ids)
        exclude = menu.addAction(_TR("从统计中排除")) if anyincluded else None
        include = menu.addAction(_TR("重新计入统计")) if anyexcluded else None
        action = menu.exec(self.table.viewport().mapToGlobal(pos))
        if action is None or action not in (exclude, include):
            return
        gobject.base.somedatabase.setsessionexcluded(ids, action is exclude)
        self.__key = None
        self.refresh(self.__gameuid)

    def refresh(self, gameuid: "str|None"):
        sessions = gobject.base.somedatabase.querysessions(gameuid)
        key = (
            gameuid,
            len(sessions),
            sessions[0][3] if sessions else 0,
            sum(_[7] for _ in sessions),
        )
        if key == self.__key:
            return
        self.__key = key
        self.__gameuid = gameuid
        self.__sessions = sessions
        self.__fillsummary(sessions)
        self.__fillchart(sessions)
        scroll = self.table.verticalScrollBar().value()
        self.__filltable(sessions, showgame=not gameuid)
        # 等表格重新布局（滚动范围更新）后再恢复位置
        QTimer.singleShot(0, lambda: self.table.verticalScrollBar().setValue(scroll))

    def __fillchart(self, sessions):
        valid = _counted(sessions)
        n = CHART_COUNTS[self.count.currentIndex()]
        if n:
            valid = valid[:n]
        # querysessions 新的在前；图表按时间从旧到新
        self.chart.setdata([(_[2], sessioncpm(_[2], _[3], _[4])) for _ in valid])

    def __fillsummary(self, sessions):
        valid = _counted(sessions)
        if not valid:
            self.summary.setText(_TR("暂无会话"))
            return
        parts = []
        parts.append(
            "{}: {} · {}".format(
                _TR("会话数"),
                len(valid),
                formatduration(sum(_[3] - _[2] for _ in valid)),
            )
        )
        parts.append("{}: {:.0f} CPM".format(_TR("平均"), _weightedcpm(valid)))
        recent = _weightedcpm(valid[:10])
        parts.append("{}: {:.0f} CPM".format(_TR("最近10次"), recent))
        best = max(valid, key=lambda _: sessioncpm(_[2], _[3], _[4]))
        parts.append(
            "{}: {:.0f} CPM ({})".format(
                _TR("最佳"),
                sessioncpm(best[2], best[3], best[4]),
                datetime.fromtimestamp(best[2]).strftime("%Y-%m-%d"),
            )
        )
        self.summary.setText("   ·   ".join(parts))

    def __filltable(self, sessions, showgame):
        self.model.clear()
        headers = ["日期"]
        if showgame:
            headers.append("游戏")
        headers += ["时长", "字数", "CPM"]
        # 末尾空列吸收多余宽度
        self.model.setHorizontalHeaderLabels(headers + [""])
        for _id, uid, start, stop, wc, lines, estimated, excluded in sessions:
            row = []
            date = datetime.fromtimestamp(start).strftime("%Y-%m-%d %H:%M")
            dateitem = _item(date + (" *" if estimated else ""))
            dateitem.setData(_id, SESSIONIDROLE)
            tips = []
            if estimated:
                tips.append(_TR("估算"))
            if excluded:
                tips.append(_TR("已从统计中排除"))
            if tips:
                dateitem.setToolTip("\n".join(tips))
            row.append(dateitem)
            if showgame:
                row.append(_item(savehook_new_data.get(uid, {}).get("title", "")))
            duration = stop - start
            row.append(_item(formatduration(duration), right=True))
            row.append(_item(str(wc), right=True))
            cpm = sessioncpm(start, stop, wc) if duration >= 60 else None
            row.append(_item("{:.0f}".format(cpm) if cpm else "—", right=True))
            if duration < SESSION_MIN or excluded:
                for item in row:
                    item.setForeground(
                        self.palette().color(QPalette.ColorRole.PlaceholderText)
                    )
            if excluded:
                for item in row:
                    font = item.font()
                    font.setStrikeOut(True)
                    item.setFont(font)
            self.model.appendRow(row)
        # 列宽按内容，CPM 紧跟在字数后面；多余空间放在最后一个空列
        header = self.table.horizontalHeader()
        header.setMinimumSectionSize(70)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(len(headers), QHeaderView.ResizeMode.Stretch)
