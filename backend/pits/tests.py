import json
import threading
import urllib.error
import urllib.request
from socketserver import ThreadingMixIn
from wsgiref import simple_server

from django.core.handlers.wsgi import WSGIHandler
from django.test import Client, TransactionTestCase

from pits.auth import make_token
from pits.models import LiquorSample, Pit, User, Yard


class ThreadingWSGIServer(ThreadingMixIn, simple_server.WSGIServer):
    daemon_threads = True


def make_yard():
    yard = Yard.objects.create(name="南冈鞣场", village="青皮村")
    layout = [
        ("东-1", Pit.STATUS_TANNING, 0, 0, 4.2),
        ("东-2", Pit.STATUS_FILL, 0, 1, None),
        ("中-1", Pit.STATUS_DRAINED, 1, 0, 4.6),
        ("中-2", Pit.STATUS_TANNING, 1, 1, 6.1),
        ("西-1", Pit.STATUS_FILL, 2, 0, None),
        ("西-2", Pit.STATUS_DRAINED, 2, 1, 3.8),
    ]
    pits = {}
    for code, status, row, col, ph in layout:
        pit = Pit.objects.create(yard=yard, code=code, status=status, row=row, col=col)
        pits[code] = pit
        if ph is not None:
            LiquorSample.objects.create(pit=pit, ph=ph, operator="worker")
    return yard, pits


class PitApiTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.user = User.objects.create(username="worker", role="worker")
        self.user.set_password("123456")
        self.user.save()
        self.yard, self.pits = make_yard()
        self.client = Client()
        self.token = make_token("worker")

    def post_json(self, url, body):
        return self.client.post(
            url, data=json.dumps(body), content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

    def test_click_west_column_writes_west_pit(self):
        """点西列坑 → 抽屉开西列坑 → 写入必须进西列那口坑，贴片等于库里最近读数。"""
        west = self.pits["西-1"]
        before = {p.id: p.samples.count() for p in self.pits.values()}

        res = self.post_json(f"/api/pits/{west.id}/samples", {"ph": 4.7})
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()

        self.assertEqual(data["id"], west.id)
        self.assertEqual(data["latestPh"], 4.7)
        self.assertEqual(data["sampleCount"], before[west.id] + 1)
        self.assertEqual(list(west.samples.values_list("ph", flat=True)), [4.7])
        # 除了被点的西列坑只 +1，其余各坑条数一律不变（绝不串到邻列）
        for code, pit in self.pits.items():
            expected = before[pit.id] + (1 if pit.id == west.id else 0)
            self.assertEqual(pit.samples.count(), expected, f"{code} 条数异常")

    def test_each_column_pit_is_independent(self):
        """逐一点每一列的坑，写入都只落在该坑，绝不落到 col-1 邻列。"""
        ordered = [self.pits["东-1"], self.pits["东-2"], self.pits["西-1"], self.pits["西-2"]]
        for i, pit in enumerate(ordered, start=1):
            baseline = {p.id: p.samples.count() for p in ordered}
            res = self.post_json(f"/api/pits/{pit.id}/samples", {"ph": 4.0 + i / 10})
            self.assertEqual(res.status_code, 200, res.content)
            self.assertEqual(res.json()["id"], pit.id)
            for other in ordered:
                expected = baseline[other.id] + (1 if other.id == pit.id else 0)
                self.assertEqual(other.samples.count(), expected, f"{pit.code} 串写到 {other.code}")

    def test_board_badge_matches_latest_sample(self):
        west = self.pits["西-1"]
        self.post_json(f"/api/pits/{west.id}/samples", {"ph": 3.9})
        res = self.client.get("/api/board", HTTP_AUTHORIZATION=f"Bearer {self.token}")
        card = next(p for p in res.json()["pits"] if p["id"] == west.id)
        self.assertEqual(card["latestPh"], 3.9)

    def test_invalid_ph_string_is_rejected_in_chinese(self):
        west = self.pits["西-1"]
        before = LiquorSample.objects.count()
        res = self.post_json(f"/api/pits/{west.id}/samples", {"ph": "abc"})
        self.assertEqual(res.status_code, 400)
        detail = res.json()["detail"]
        self.assertIn("数字", detail)
        self.assertTrue(any("一" <= ch <= "鿿" for ch in detail))
        self.assertEqual(LiquorSample.objects.count(), before)
        self.assertEqual(west.samples.count(), 0)

    def test_invalid_ph_out_of_range_is_rejected(self):
        west = self.pits["西-1"]
        for bad in (99, -3.2):
            res = self.post_json(f"/api/pits/{west.id}/samples", {"ph": bad})
            self.assertEqual(res.status_code, 400, bad)
            self.assertIn("酸碱度", res.json()["detail"])
        self.assertEqual(west.samples.count(), 0)

    def test_missing_ph_is_rejected(self):
        west = self.pits["西-1"]
        res = self.post_json(f"/api/pits/{west.id}/samples", {})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(west.samples.count(), 0)

    def test_status_change_does_not_touch_samples(self):
        """改坑态不读贴片规矩：状态流转不得增删改任何酸碱记录。"""
        pit = self.pits["中-1"]  # 最近 4.6，已放液
        snapshot = list(pit.samples.values("ph", "operator", "taken_at"))

        res = self.post_json(f"/api/pits/{pit.id}/status", {"status": "fill"})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["status"], "fill")
        self.assertEqual(list(pit.samples.values("ph", "operator", "taken_at")), snapshot)

        res = self.post_json(f"/api/pits/{pit.id}/status", {"status": "drained"})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(list(pit.samples.values("ph", "operator", "taken_at")), snapshot)

    def test_drained_blocked_uses_db_latest_ph(self):
        bad = self.pits["中-2"]  # 最近 6.1，超 5.0
        res = self.post_json(f"/api/pits/{bad.id}/status", {"status": "drained"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("6.1", res.json()["detail"])
        bad.refresh_from_db()
        self.assertEqual(bad.status, Pit.STATUS_TANNING)
        self.assertEqual(bad.samples.count(), 1)


class _QuietHandler(simple_server.WSGIRequestHandler):
    def log_message(self, *args):
        pass


class ConcurrentSampleTests(TransactionTestCase):
    """两名工抢着给两口不同列的坑写酸碱：各坑只许多一条，禁止写到邻列。"""

    reset_sequences = True

    def setUp(self):
        self.httpd = simple_server.make_server(
            "127.0.0.1", 0, WSGIHandler(),
            server_class=ThreadingWSGIServer, handler_class=_QuietHandler,
        )
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def test_concurrent_writes_stay_in_own_pits(self):
        user = User.objects.create(username="worker", role="worker")
        user.set_password("123456")
        user.save()
        yard, pits = make_yard()
        token = make_token("worker")

        col0 = pits["东-1"]   # row 0, col 0（原本已有 1 条）
        col1 = pits["东-2"]   # row 0, col 1（原本 0 条）——旧 bug 会把 col1 的写入挪到 col0
        start = threading.Barrier(2)
        errors = []

        def send(pit, ph):
            req = urllib.request.Request(
                f"{self.base}/api/pits/{pit.id}/samples",
                data=json.dumps({"ph": ph}).encode(),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
                method="POST",
            )
            start.wait()
            try:
                with urllib.request.urlopen(req, timeout=30) as res:
                    data = json.loads(res.read())
                    assert data["id"] == pit.id
                    assert data["latestPh"] == ph
            except urllib.error.HTTPError as exc:
                errors.append(f"{pit.code}: HTTP {exc.code} {exc.read().decode()[:300]}")
            except (urllib.error.URLError, AssertionError) as exc:
                errors.append(exc)

        t1 = threading.Thread(target=send, args=(col0, 3.6))
        t2 = threading.Thread(target=send, args=(col1, 4.9))
        t1.start(); t2.start(); t1.join(); t2.join()
        self.assertEqual(errors, [])

        col0.refresh_from_db(); col1.refresh_from_db()
        self.assertEqual(col0.samples.count(), 2, "col0 只许多一条")
        self.assertEqual(col1.samples.count(), 1, "col1 必须拿到自己的那一条")
        self.assertEqual(col1.samples.latest("id").ph, 4.9)
        # 其余坑位条数与种子一致，没有邻列串写
        for code, expected in (("东-1", 2), ("东-2", 1), ("中-1", 1), ("中-2", 1), ("西-1", 0), ("西-2", 1)):
            self.assertEqual(pits[code].samples.count(), expected, f"{code} 条数异常")
