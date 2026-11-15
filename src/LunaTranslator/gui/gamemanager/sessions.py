from qtsymbols import *
from datetime import datetime
import gobject
from myutils.config import _TR, savehook_new_data
from myutils.somedatabase import SESSION_MIN, formatduration, sessioncpm
from gui.dynalang import LTableView, LStandardItemModel


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
        lay.addWidget(self.summary)
        self.model = LStandardItemModel(self)
        self.table = LTableView(self)
        self.table.setModel(self.model)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.verticalHeader().hide()
        self.table.setMinimumHeight(200)
        lay.addWidget(self.table)
        self.__key = None

    def refresh(self, gameuid: "str|None"):
        sessions = gobject.base.somedatabase.querysessions(gameuid)
        key = (gameuid, len(sessions), sessions[0][3] if sessions else 0)
        if key == self.__key:
            return
        self.__key = key
        self.__fillsummary(sessions)
        self.__filltable(sessions, showgame=not gameuid)

    def __fillsummary(self, sessions):
        valid = [_ for _ in sessions if _[3] - _[2] >= SESSION_MIN]
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
        self.model.setHorizontalHeaderLabels(headers)
        for _id, uid, start, stop, wc, lines, estimated in sessions:
            row = []
            date = datetime.fromtimestamp(start).strftime("%Y-%m-%d %H:%M")
            dateitem = _item(date + (" *" if estimated else ""))
            if estimated:
                dateitem.setToolTip(_TR("估算"))
            row.append(dateitem)
            if showgame:
                row.append(_item(savehook_new_data.get(uid, {}).get("title", "")))
            duration = stop - start
            row.append(_item(formatduration(duration), right=True))
            row.append(_item(str(wc), right=True))
            cpm = sessioncpm(start, stop, wc) if duration >= 60 else None
            row.append(_item("{:.0f}".format(cpm) if cpm else "—", right=True))
            if duration < SESSION_MIN:
                for item in row:
                    item.setForeground(
                        self.palette().color(QPalette.ColorRole.PlaceholderText)
                    )
            self.model.appendRow(row)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        if showgame:
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        else:
            header.setStretchLastSection(True)
