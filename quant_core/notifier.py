"""
quant_core/notifier.py
새로 매수 구간에 들어온 종목이 있을 때만 알림을 보낸다 (텔레그램 · 메일).

설정은 환경 변수(GitHub Actions Secrets)로만 받는다. 아무것도 설정하지 않으면 조용히 건너뛴다.
  텔레그램: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  메일    : ALERT_EMAIL_TO, SMTP_USER, SMTP_PASSWORD  (선택: SMTP_HOST=smtp.gmail.com, SMTP_PORT=465)
  공통    : APP_URL (알림 끝에 붙일 앱 주소, 선택)
토큰·비밀번호는 로그에 남기지 않는다.
"""

import os
import json
import smtplib
import urllib.request
from email.mime.text import MIMEText
from typing import Any, Dict, List

MAX_ITEMS = 12      # 한 번에 자세히 보여줄 종목 수 (나머지는 '외 N개')


def _money(x: float) -> str:
    return f"${x:,.2f}" if x >= 1 else f"${x:,.4f}"


def _merge_by_ticker(setups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """같은 종목의 주봉·일봉은 하나로 (주봉 우선)."""
    out: Dict[str, Dict[str, Any]] = {}
    for s in sorted(setups, key=lambda x: x['tf'] != '1w'):
        if s['ticker'] in out:
            out[s['ticker']]['also'].append(s['tf_label'])
        else:
            out[s['ticker']] = dict(s, also=[])
    return list(out.values())


def build_message(new_setups: List[Dict[str, Any]], date: str, liquid: int = 0,
                  app_url: str = '') -> Dict[str, str]:
    items = _merge_by_ticker(new_setups)
    subject = f"[패턴 구간] 새 추천 {len(items)}종목 ({date})"
    lines = [f"📐 패턴 구간 새 추천 · {date} 종가 기준",
             f"나스닥 {liquid:,}종목 중 새로 {len(items)}종목이 매수 구간에 들어왔습니다." if liquid
             else f"새로 {len(items)}종목이 매수 구간에 들어왔습니다.", ""]
    for s in items[:MAX_ITEMS]:
        tf = s['tf_label'] + (' · ' + '·'.join(a + '도' for a in s['also']) if s['also'] else '')
        lines.append(f"{s['ticker']} · {tf} · {s.get('position', '구간 안')}")
        lines.append(f"  현재 {_money(s['price'])} · 매수 {_money(s['buy_low'])}~{_money(s['buy_high'])}")
        lines.append(f"  손절 {_money(s['stop'])} ({s['stop_pct']:+.1f}%) · 1차 {_money(s['t1'])} ({s['t1_pct']:+.1f}%)")
        if s.get('earnings_soon'):
            lines.append(f"  ⚠ 실적 발표 D-{s['earnings_days']}")
        lines.append("")
    if len(items) > MAX_ITEMS:
        lines.append(f"외 {len(items) - MAX_ITEMS}종목은 앱에서 확인하세요.")
    lines.append("매수 추천이 아니라 검토 후보입니다. 차트를 직접 확인하세요.")
    if app_url:
        lines.append(app_url)
    return {'subject': subject, 'text': "\n".join(lines).strip()}


def send_telegram(text: str) -> bool:
    token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    chat_id = os.getenv('TELEGRAM_CHAT_ID', '').strip()
    if not token or not chat_id:
        return False
    body = json.dumps({'chat_id': chat_id, 'text': text[:4000],
                       'disable_web_page_preview': True}).encode('utf-8')
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return 200 <= resp.status < 300


def send_email(subject: str, text: str) -> bool:
    to = os.getenv('ALERT_EMAIL_TO', '').strip()
    user = os.getenv('SMTP_USER', '').strip()
    pw = os.getenv('SMTP_PASSWORD', '').strip()
    if not (to and user and pw):
        return False
    host = os.getenv('SMTP_HOST', 'smtp.gmail.com').strip() or 'smtp.gmail.com'
    port = int(os.getenv('SMTP_PORT', '465') or 465)
    msg = MIMEText(text, 'plain', 'utf-8')
    msg['Subject'] = subject
    msg['From'] = user
    msg['To'] = to
    with smtplib.SMTP_SSL(host, port, timeout=30) as s:
        s.login(user, pw)
        s.sendmail(user, [a.strip() for a in to.split(',') if a.strip()], msg.as_string())
    return True


def notify_new_setups(new_setups: List[Dict[str, Any]], date: str, liquid: int = 0) -> List[str]:
    """새 추천이 있을 때만 설정된 채널로 보낸다. 보낸 채널 이름 목록을 돌려준다."""
    if not new_setups:
        return []
    msg = build_message(new_setups, date, liquid, os.getenv('APP_URL', '').strip())
    sent = []
    for name, fn in (('telegram', lambda: send_telegram(msg['text'])),
                     ('email', lambda: send_email(msg['subject'], msg['text']))):
        try:
            if fn():
                sent.append(name)
        except Exception as exc:
            # 비밀값이 섞이지 않도록 예외 종류만 남긴다
            print(f"  [알림] {name} 전송 실패: {type(exc).__name__}")
    return sent
