import os
import sys

# vercel/api 를 import 경로에 넣어 handler 모듈(_common, notify, telegram)을 로드 가능하게 한다.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "api")))
