import json

from django.test import TestCase
from django.utils import timezone

from pits.models import LiquorSample, Pit, User, Yard


def post_json(client, url, body, token=None):
    headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
    return client.post(url, data=json.dumps(body), content_type="application/json", **headers)


class BoardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yard = Yard.objects.create(name="南冈鞣场", village="青皮村")
        cls.admin = User.objects.create(username="admin", role="admin")
        cls.admin.set_password("123456")
        cls.admin.save()
        cls.worker = User.objects.create(username="worker", role="worker")
        cls.worker.set_password("123456")
        cls.worker.save()
        # 东中西北方位用 col 表示：col=0 东列，col=1 西列
        cls.east = Pit.objects.create(yard=cls.yard, code="东坑", status="fill", row=0, col=0)
        cls.west = Pit.objects.create(yard=cls.yard, code="西坑", status="fill", row=0, col=1)

    def login(self, username, password="123456"):
        resp = self.client.post(
            "/api/auth/login",
            data=json.dumps({"username": username, "password": password}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        return resp.json()["access_token"]

    def board(self, token):
        resp = self.client.get("/api/board", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(resp.status_code, 200, resp.content)
        return resp.json()

    # 点西列色块必须开西列那口坑：后端返回的坑位身份按 id 区分，写入也按 id 落本坑
    def test_sample_writes_to_requested_pit_not_neighbor(self):
        token = self.login("worker")
        resp = post_json(self.client, f"/api/pits/{self.west.id}/samples", {"ph": 4.3}, token)
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertEqual(data["id"], self.west.id)
        self.assertEqual(data["latestPh"], 4.3)
        self.assertEqual(data["sampleCount"], 1)
        # 东列邻坑不得多一条
        self.assertEqual(LiquorSample.objects.filter(pit=self.east).count(), 0)
        self.assertEqual(LiquorSample.objects.filter(pit=self.west).count(), 1)

    def test_latest_ph_is_most_recent_of_that_pit(self):
        token = self.login("admin")
        LiquorSample.objects.create(pit=self.west, ph=4.2, operator="seed")
        # 东坑塞一个更晚的读数，验证 latestPh 只读本坑，不串
        LiquorSample.objects.create(pit=self.east, ph=6.1, operator="seed")
        resp = post_json(self.client, f"/api/pits/{self.west.id}/samples", {"ph": 3.9}, token)
        self.assertEqual(resp.json()["latestPh"], 3.9)
        board = self.board(token)
        west = next(p for p in board["pits"] if p["id"] == self.west.id)
        self.assertEqual(west["latestPh"], 3.9)

    def test_two_workers_two_pits_each_gets_one_row(self):
        t1 = self.login("admin")
        t2 = self.login("worker")
        r1 = post_json(self.client, f"/api/pits/{self.east.id}/samples", {"ph": 4.4}, t1)
        r2 = post_json(self.client, f"/api/pits/{self.west.id}/samples", {"ph": 3.7}, t2)
        self.assertEqual(r1.status_code, 200, r1.content)
        self.assertEqual(r2.status_code, 200, r2.content)
        self.assertEqual(r1.json()["id"], self.east.id)
        self.assertEqual(r2.json()["id"], self.west.id)
        self.assertEqual(LiquorSample.objects.filter(pit=self.east).count(), 1)
        self.assertEqual(LiquorSample.objects.filter(pit=self.west).count(), 1)

    def test_invalid_ph_string_rejected_in_chinese_and_not_saved(self):
        token = self.login("worker")
        for bad in ("酸", None, {}, [], True):
            resp = post_json(self.client, f"/api/pits/{self.west.id}/samples", {"ph": bad}, token)
            self.assertEqual(resp.status_code, 400, (bad, resp.content))
            detail = resp.json()["detail"]
            self.assertTrue(any("一" <= ch <= "鿿" for ch in detail), detail)
        self.assertEqual(LiquorSample.objects.count(), 0)

    def test_nan_infinity_out_of_range_rejected(self):
        token = self.login("worker")

        def send(raw):
            return self.client.generic(
                "POST",
                f"/api/pits/{self.west.id}/samples",
                raw,
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {token}",
            )

        for bad in ("NaN", "Infinity", "-Infinity", "99", "-1", "15"):
            resp = send(json.dumps({"ph": float(bad)}) if bad[0].isalpha() else json.dumps({"ph": float(bad)}))
            self.assertEqual(resp.status_code, 400, (bad, resp.status_code, resp.content))
        self.assertEqual(LiquorSample.objects.count(), 0)

    def test_status_rules_do_not_read_samples(self):
        # 改坑态：非法状态中文 400；放液门槛仍按规矩走，登记接口不掺状态逻辑
        token = self.login("admin")
        resp = post_json(self.client, f"/api/pits/{self.east.id}/status", {"status": "坏了"}, token)
        self.assertEqual(resp.status_code, 400)
        # 无记录放液被拦
        resp = post_json(self.client, f"/api/pits/{self.east.id}/status", {"status": "drained"}, token)
        self.assertEqual(resp.status_code, 400)
        post_json(self.client, f"/api/pits/{self.east.id}/samples", {"ph": 4.0}, token)
        resp = post_json(self.client, f"/api/pits/{self.east.id}/status", {"status": "drained"}, token)
        self.assertEqual(resp.status_code, 200, resp.content)

    def test_session_survives_pit_selection(self):
        # 点列只改前端 picked，token 仍有效：同一 token 连续访问 board 不受影响
        token = self.login("worker")
        for _ in range(3):
            resp = self.client.get("/api/board", HTTP_AUTHORIZATION=f"Bearer {token}")
            self.assertEqual(resp.status_code, 200)
