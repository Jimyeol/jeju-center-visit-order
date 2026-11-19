import unittest

from app.korean import attach, josa


class JosaTest(unittest.TestCase):
    def test_topic_and_subject(self):
        self.assertEqual(josa("노형동", "은"), "은")
        self.assertEqual(josa("카페", "은"), "는")
        self.assertEqual(josa("한식 음식점업", "이"), "이")
        self.assertEqual(josa("애월", "가"), "이")
        self.assertEqual(josa("서귀포", "이"), "가")

    def test_object(self):
        self.assertEqual(josa("카페", "을"), "를")
        self.assertEqual(josa("3단계 받은 파일 분석", "를"), "을")

    def test_direction(self):
        self.assertEqual(josa("조천읍", "로"), "으로")
        self.assertEqual(josa("제주시", "으로"), "로")
        self.assertEqual(josa("용담일동", "로"), "으로")
        self.assertEqual(josa("하귀일리", "로"), "로")
        self.assertEqual(josa("제주시 일도1동", "로"), "으로")

    def test_quotes_and_brackets(self):
        self.assertEqual(josa("'함덕'", "은"), "은")
        self.assertEqual(josa("NA(알 수 없음)", "은"), "은")
        self.assertEqual(josa("카드 이용 현황(시간대)_v02", "을"), "를")

    def test_attach(self):
        self.assertEqual(attach("노형동", "은"), "노형동은")
        self.assertEqual(attach("애월읍", "로"), "애월읍으로")
        self.assertEqual(attach("", "은"), "는")


if __name__ == "__main__":
    unittest.main()
