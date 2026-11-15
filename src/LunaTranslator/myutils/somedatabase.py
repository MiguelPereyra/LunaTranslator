import sqlite3, gobject, time, windows, NativeUtils
import time
import os
from qtsymbols import *
from myutils.config import findgameuidofpath, savehook_new_data, _TR
from myutils.wrapper import threader
import threading
from queue import Queue
import windows
import gobject
from traceback import print_exc

# 两行文本间隔超过这个时间（秒）就算新的阅读会话
SESSION_GAP = 30 * 60
# 短于这个时间（秒）的会话不参与“最佳”等统计，也不弹出总结
SESSION_MIN = 5 * 60


def formatduration(seconds: float) -> str:
    minutes = int(round(seconds / 60))
    if minutes < 60:
        return "{} min".format(minutes)
    return "{} h {:02d} min".format(minutes // 60, minutes % 60)


def sessioncpm(start: float, stop: float, wordcount: int) -> float:
    minutes = (stop - start) / 60
    if minutes <= 0:
        return 0
    return wordcount / minutes


class somedatabase:
    def all(self):
        res = self.sqlsavegameinfo.execute(
            "SELECT gameinternalid_v2.gameuid, trace_strict.timestart, trace_strict.timestop FROM gameinternalid_v2 JOIN trace_strict ON gameinternalid_v2.gameinternalid = trace_strict.gameinternalid "
        ).fetchall()
        mp: "dict[str, list]" = {}
        for uid, s, e in res:
            if uid not in mp:
                mp[uid] = []
            mp[uid].append((s, e))
        return mp

    def append_word(self, word, sentence=None):
        sentence = sentence if sentence else ""
        self.sqlsavegameinfo.execute(
            "INSERT INTO search_word_history VALUES(NULL,?,?,?,?)".format(),
            (word, sentence, time.time(), 0),
        )

    def removewhich(self, _id):
        self.sqlsavegameinfo.execute(
            "DELETE FROM search_word_history WHERE id=?", (_id,)
        )

    def allwords(self):
        return self.sqlsavegameinfo.execute(
            "SELECT * FROM search_word_history ORDER BY id DESC"
        ).fetchall()

    def __init__(self):
        self._cache = {}
        self._cache2 = {}
        self.wordcountqueue = Queue()
        self.locked = threading.Lock()
        self.sqlsavegameinfo = sqlite3.connect(
            gobject.getconfig("savegame.db"),
            check_same_thread=False,
            isolation_level=None,
        )
        try:
            self.sqlsavegameinfo.execute(
                "CREATE TABLE search_word_history(id INTEGER PRIMARY KEY AUTOINCREMENT,WORD TEXT, SENTENCE TEXT ,timestamp BIGINT, status INT);"
            )
        except:
            pass
        try:
            self.sqlsavegameinfo.execute(
                "CREATE TABLE gameinternalid_v2(gameinternalid INTEGER PRIMARY KEY AUTOINCREMENT,gameuid TEXT);"
            )
        except:
            pass
        try:
            self.sqlsavegameinfo.execute(
                "CREATE TABLE trace_strict(gameinternalid INT,timestart BIGINT,timestop BIGINT);"
            )
        except:
            pass
        try:
            self.sqlsavegameinfo.execute(
                "CREATE TABLE game_word_count(gameinternalid INT,time BIGINT,wordcount BIGINT);"
            )
        except:
            pass
        # 阅读会话：从第一行文本到游戏关闭（或超过 SESSION_GAP 没有新文本）
        self.currentsession: "dict | None" = None
        try:
            self.sqlsavegameinfo.execute(
                "CREATE TABLE game_sessions(id INTEGER PRIMARY KEY AUTOINCREMENT,gameinternalid INT,timestart BIGINT,timestop BIGINT,wordcount BIGINT,linecount INT,estimated INT,excluded INT DEFAULT 0);"
            )
            created = True
        except:
            created = False
            # 旧表没有 excluded 列（手动排除出统计的会话）
            try:
                self.sqlsavegameinfo.execute(
                    "ALTER TABLE game_sessions ADD COLUMN excluded INT DEFAULT 0"
                )
            except:
                pass
        if created:
            try:
                self.rebuildsessions()
            except:
                print_exc()

        self.checkgameplayingthread()
        self.wordcountthread()

    def lockdata(self):
        self.locked.acquire()

    def unlockdata(self):
        self.locked.release()

    def settraceplaytime(self, gameuid, lst: "list[tuple[float, float]]"):
        table = "trace_strict"
        gameinternalid = self.__get_gameinternalid(gameuid)
        self.sqlsavegameinfo.execute(
            "DELETE FROM {} WHERE gameinternalid = ?".format(table), (gameinternalid,)
        )
        for s, e in lst:
            self.sqlsavegameinfo.execute(
                "INSERT INTO {} VALUES(?,?,?)".format(table),
                (gameinternalid, s, e),
            )

    def querytraceplaytime(
        self, gameuid: "str | None"
    ) -> "list[tuple[float, float]|tuple[float, float, str]]":
        if gameuid:
            gameinternalid = self.__get_gameinternalid(gameuid)
            __ = self.sqlsavegameinfo.execute(
                "SELECT timestart,timestop FROM trace_strict WHERE gameinternalid = ?",
                (gameinternalid,),
            ).fetchall()
        else:
            ___ = self.sqlsavegameinfo.execute(
                "SELECT timestart,timestop,gameinternalid FROM trace_strict"
            ).fetchall()
            __ = []
            for timestart, timestop, gameinternalid in ___:
                __id = self.__get_gameuid(gameinternalid)
                __.append((timestart, timestop, __id))
        __ = tuple(_ for _ in __ if _[1] > _[0])
        return __

    def querywordcount(
        self, gameuid: "str|None"
    ) -> "list[tuple[float, int]|tuple[float, int, str]]":
        if gameuid:
            gameinternalid = self.__get_gameinternalid(gameuid)
            __ = self.sqlsavegameinfo.execute(
                "SELECT time,wordcount FROM game_word_count WHERE gameinternalid = ?",
                (gameinternalid,),
            ).fetchall()
        else:
            ___ = self.sqlsavegameinfo.execute(
                "SELECT time,wordcount,gameinternalid FROM game_word_count"
            ).fetchall()
            __ = []
            for _time, wordcount, gameinternalid in ___:
                __id = self.__get_gameuid(gameinternalid)
                __.append((_time, wordcount, __id))
        __ = tuple(_ for _ in __ if _[1] > 0)
        return __

    def __get_gameuid(self, internalid):
        if internalid not in self._cache:
            ret = self.sqlsavegameinfo.execute(
                "SELECT gameuid FROM gameinternalid_v2 WHERE gameinternalid = ?",
                (internalid,),
            ).fetchone()
            if not ret:
                return None
            self._cache[internalid] = ret[0]
        if self._cache[internalid] not in savehook_new_data:
            return None
        return self._cache[internalid]

    def __get_gameinternalid(self, gameuid):
        if gameuid in self._cache2:
            return self._cache2[gameuid]
        while True:
            ret = self.sqlsavegameinfo.execute(
                "SELECT * FROM gameinternalid_v2 WHERE gameuid = ?",
                (gameuid,),
            ).fetchone()
            if ret is None:
                self.sqlsavegameinfo.execute(
                    "INSERT INTO gameinternalid_v2 VALUES(NULL,?)", (gameuid,)
                )
            else:
                self._cache2[gameuid] = ret[0]
                return ret[0]

    def stricttraceexe(self):
        hwnd = windows.GetForegroundWindow()
        pid = windows.GetWindowThreadProcessId(hwnd)
        exe = NativeUtils.GetProcessFileName(pid)
        exes = set()
        exes.add(exe)

        gamehwnd = gobject.base.hwnd
        if gamehwnd:
            gamepid = windows.GetWindowThreadProcessId(gamehwnd)
            if gamepid and pid == os.getpid():
                exes.add(NativeUtils.GetProcessFileName(gamepid))
        return exes

    def finduids(self, exes):
        uids = []
        for exe in exes:
            uids.extend(findgameuidofpath(exe, True))
        isemus = False
        for uid in uids:
            if savehook_new_data[uid].get("emugameid"):
                isemus = True
                break
        try:
            cureumgameid = gobject.base.textsource.emugameid
        except:
            cureumgameid = None
        if isemus:
            for uid in uids.copy():
                _emugameid = savehook_new_data[uid].get("emugameid")
                if _emugameid and (_emugameid != cureumgameid):
                    uids.remove(uid)
        return uids

    def tracex(self, _t: float, uids: list, dic: dict, table: str):
        for uid in uids:
            gameinternalid = self.__get_gameinternalid(uid)
            if uid in dic:
                self.sqlsavegameinfo.execute(
                    "UPDATE {} SET timestop = ? WHERE (gameinternalid = ? and timestart = ?)".format(
                        table
                    ),
                    (_t, gameinternalid, dic[uid]),
                )

            else:
                dic[uid] = _t
                self.sqlsavegameinfo.execute(
                    "INSERT INTO {} VALUES(?,?,?)".format(table),
                    (gameinternalid, _t, _t),
                )
        for k in list(dic.keys()):
            if k not in uids:
                dic.pop(k)

    @threader
    def checkgameplayingthread(self):
        self.trace_strict = {}
        tlast = None
        t = time.time()
        while True:
            with self.locked:
                tlast = t
                t = time.time()
                if t - tlast > 20:
                    # 虚拟机暂停
                    self.trace_strict.clear()
                    continue
                self.tracex(
                    t,
                    self.finduids(self.stricttraceexe()),
                    self.trace_strict,
                    "trace_strict",
                )
            time.sleep(10)

    @threader
    def wordcountthread(self):
        lastuid = None
        while True:
            t, uid, wc = self.wordcountqueue.get()
            if wc is None:
                # 游戏进程已结束（texthook.removeproc 放入的）
                try:
                    self.__endsession(uid, t)
                except:
                    print_exc()
                continue
            if not uid:
                continue
            if lastuid != uid:
                gameinternalid = self.__get_gameinternalid(uid)
                lastuid = uid
            self.sqlsavegameinfo.execute(
                "INSERT INTO game_word_count VALUES(?,?,?)",
                (gameinternalid, t, wc),
            )
            try:
                self.__sessionadd(uid, t, wc)
            except:
                print_exc()

    def gameended(self, uid):
        # 走同一个队列，保证在最后几行文本之后处理
        if uid:
            self.wordcountqueue.put((time.time(), uid, None))

    def __sessionadd(self, uid, t, wc):
        s = self.currentsession
        if s and (s["uid"] != uid or t - s["last"] > SESSION_GAP):
            self.__endsession(None, None)
            s = None
        if s is None:
            cur = self.sqlsavegameinfo.execute(
                "INSERT INTO game_sessions(gameinternalid,timestart,timestop,wordcount,linecount,estimated) VALUES(?,?,?,?,?,0)",
                (self.__get_gameinternalid(uid), t, t, wc, 1),
            )
            self.currentsession = dict(
                id=cur.lastrowid, uid=uid, start=t, last=t, wc=wc, lines=1
            )
            return
        s["last"] = t
        s["wc"] += wc
        s["lines"] += 1
        # 每行都写入，Luna 意外退出时会话也能保留（结束于最后一行）
        self.sqlsavegameinfo.execute(
            "UPDATE game_sessions SET timestop=?, wordcount=?, linecount=? WHERE id=?",
            (t, s["wc"], s["lines"], s["id"]),
        )

    def __endsession(self, uid, t):
        # uid/t 为 None：因换游戏或长时间无文本而结束，结束时间取最后一行
        s = self.currentsession
        if not s:
            return
        if uid is not None and uid != s["uid"]:
            return
        self.currentsession = None
        gameclosed = t is not None and t - s["last"] <= SESSION_GAP
        stop = max(t, s["last"]) if gameclosed else s["last"]
        self.sqlsavegameinfo.execute(
            "UPDATE game_sessions SET timestop=? WHERE id=?", (stop, s["id"])
        )
        if gameclosed and stop - s["start"] >= SESSION_MIN:
            self.__notifysession(s["uid"], s["id"], s["start"], stop, s["wc"])

    def __notifysession(self, uid, sid, start, stop, wc):
        cpm = sessioncpm(start, stop, wc)
        # 与统计页一致：不算本次、手动排除的和过短的会话
        others = [
            _
            for _ in self.querysessions(uid)
            if _[0] != sid and not _[7] and _[3] - _[2] >= SESSION_MIN
        ]
        msg = "{} · {} {} · {:.0f} CPM".format(
            formatduration(stop - start), wc, _TR("字数").lower(), cpm
        )
        if others:
            totalwc = sum(_[4] for _ in others)
            totalmin = sum(_[3] - _[2] for _ in others) / 60
            msg += " ({} {:.0f})".format(_TR("平均"), totalwc / totalmin)
        title = _TR("会话结束")
        try:
            title += " · " + savehook_new_data[uid]["title"]
        except:
            pass
        gobject.base.displayinfomessage(title + ": " + msg, "<msg_info_refresh>")

        def _tray():
            try:
                gobject.base.showtraymessage(title, msg, lambda: None)
            except:
                pass

        gobject.base.safeinvokefunction.emit(_tray)

    def querysessions(self, gameuid: "str|None"):
        # [(id, gameuid, timestart, timestop, wordcount, linecount, estimated, excluded)]，新的在前
        sql = "SELECT id,gameinternalid,timestart,timestop,wordcount,linecount,estimated,excluded FROM game_sessions"
        args = ()
        if gameuid:
            sql += " WHERE gameinternalid = ?"
            args = (self.__get_gameinternalid(gameuid),)
        res = []
        for _id, gid, s, e, wc, n, est, exc in self.sqlsavegameinfo.execute(
            sql + " ORDER BY timestart DESC", args
        ).fetchall():
            uid = gameuid if gameuid else self.__get_gameuid(gid)
            if uid:
                res.append((_id, uid, s, e, wc, n, est, bool(exc)))
        return res

    def setsessionexcluded(self, ids: "list[int]", excluded: bool):
        self.sqlsavegameinfo.executemany(
            "UPDATE game_sessions SET excluded=? WHERE id=?",
            [(int(excluded), _id) for _id in ids],
        )

    def rebuildsessions(self):
        # 根据已有的逐行字数记录重建以前的会话（没有记录游戏关闭时间，结束取最后一行）
        rows = self.sqlsavegameinfo.execute(
            "SELECT gameinternalid,time,wordcount FROM game_word_count ORDER BY gameinternalid,time"
        ).fetchall()
        sessions = []
        for gid, t, wc in rows:
            if sessions and sessions[-1][0] == gid and t - sessions[-1][2] <= SESSION_GAP:
                last = sessions[-1]
                last[2] = t
                last[3] += wc
                last[4] += 1
            else:
                sessions.append([gid, t, t, wc, 1])
        sessions.sort(key=lambda _: _[1])
        self.sqlsavegameinfo.executemany(
            "INSERT INTO game_sessions(gameinternalid,timestart,timestop,wordcount,linecount,estimated) VALUES(?,?,?,?,?,1)",
            [tuple(_) for _ in sessions],
        )
