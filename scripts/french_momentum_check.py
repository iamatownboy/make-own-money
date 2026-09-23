"""
scripts/french_momentum_check.py
대형주 모멘텀의 장기(1927~) 증거 확인.

자체 데이터(약 12년)로는 연 1~3%p 크기의 엣지를 통계적으로 확인할 수 없다
(scripts/factor_study.py 검정력 절 참고). 그래서 근거를 수십 년짜리 학술 데이터에서 찾는다.

데이터: Kenneth R. French Data Library
  - 6 Portfolios Formed on Size and Momentum (2x3): 대형/소형 x 과거 수익률(t-12~t-2) 하위30/중위40/상위30%
    시가총액 가중, 월간. CRSP 전 종목 기반이며 상장폐지 수익률이 반영되어 생존 편향이 없다.
  - Fama/French 3 Factors: 시장 수익률(Mkt-RF + RF)

산출:
  - 대형 상위(BIG HiPRIOR) - 시장 : 롱온리 모멘텀 기울이기의 초과수익 (이 프로젝트가 실제로 할 수 있는 형태)
  - 대형 상위 - 대형 하위       : 롱숏 모멘텀 스프레드 (학술적 팩터 크기)
  - 시대별 분해와 최근 10년 롤링으로 '효과가 줄었는가'를 본다.

사용:
    python scripts/french_momentum_check.py
"""

import io
import os
import sys
import zipfile
import urllib.request

import numpy as np
import pandas as pd

BASE = 'https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/'
SIZE_MOM = '6_Portfolios_ME_Prior_12_2_CSV.zip'
FACTORS = 'F-F_Research_Data_Factors_CSV.zip'

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from scripts.factor_study import newey_west_t  # noqa: E402


def _fetch_first_monthly_block(zip_name: str) -> pd.DataFrame:
    """French CSV의 첫 번째 월간 블록(YYYYMM 행)만 파싱한다. 값은 % 단위."""
    raw = urllib.request.urlopen(BASE + zip_name, timeout=60).read()
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        text = z.read(z.namelist()[0]).decode('latin-1')
    lines = text.splitlines()
    start = next(i for i, l in enumerate(lines) if l.strip().startswith(',') and i + 1 < len(lines)
                 and lines[i + 1].strip()[:6].isdigit())
    header = [h.strip() for h in lines[start].split(',')]
    rows = []
    for l in lines[start + 1:]:
        parts = [p.strip() for p in l.split(',')]
        if not parts[0].isdigit() or len(parts[0]) != 6:
            break
        rows.append(parts)
    df = pd.DataFrame(rows, columns=['ym'] + header[1:])
    df.index = pd.PeriodIndex(df.pop('ym'), freq='M')
    df = df.astype(float).replace([-99.99, -999], np.nan) / 100
    return df


def summarize(x: pd.Series) -> str:
    x = x.dropna()
    return (f"{x.mean() * 12 * 100:+5.1f}%/년  t={newey_west_t(x):+5.2f}  "
            f"이긴 달 {(x > 0).mean() * 100:3.0f}%  ({len(x)}개월)")


def main():
    pm = _fetch_first_monthly_block(SIZE_MOM)
    ff = _fetch_first_monthly_block(FACTORS)
    mkt = ff['Mkt-RF'] + ff['RF']

    df = pd.DataFrame({
        'big_hi': pm['BIG HiPRIOR'], 'big_lo': pm['BIG LoPRIOR'], 'big_mid': pm['ME2 PRIOR2'],
        'mkt': mkt,
    }).dropna()
    df['tilt'] = df['big_hi'] - df['mkt']          # 롱온리: 대형 모멘텀 상위 30% vs 시장
    df['spread'] = df['big_hi'] - df['big_lo']     # 롱숏: 대형 상위 - 대형 하위

    print(f"Kenneth French 데이터, {df.index.min()} ~ {df.index.max()} ({len(df)}개월)")
    print("대형주 = NYSE 중위 시가총액 이상, 모멘텀 = t-12~t-2 수익률, 시가총액 가중, 상장폐지 수익률 반영\n")

    eras = [('1927-1962', '1927-01', '1962-12'), ('1963-1999', '1963-01', '1999-12'),
            ('2000-2013', '2000-01', '2013-12'), ('2014-현재', '2014-01', str(df.index.max()))]

    for col, title in [('tilt', '■ 롱온리: 대형 모멘텀 상위 30% - 시장  (이 프로젝트가 실제로 할 수 있는 형태)'),
                       ('spread', '■ 롱숏: 대형 상위 30% - 대형 하위 30%  (학술적 팩터 크기)')]:
        print(title)
        print(f"  {'전 기간':<11}{summarize(df[col])}")
        for name, a, b in eras:
            print(f"  {name:<11}{summarize(df.loc[a:b, col])}")
        print()

    # 참고: 소형주에서는 남아 있는가 (거래비용이 커서 실행 난이도는 높다)
    small = pd.DataFrame({'hi': pm['SMALL HiPRIOR'], 'lo': pm['SMALL LoPRIOR'], 'mkt': mkt}).dropna()
    print("■ (참고) 소형주 — 상위 30% - 시장 / 상위 30% - 하위 30%  (비용 차감 전)")
    for name, a, b in eras:
        s = small.loc[a:b]
        print(f"  {name:<11}롱온리 {(s.hi - s.mkt).mean() * 1200:+5.1f}%/년 (t={newey_west_t(s.hi - s.mkt):+5.2f})"
              f"   롱숏 {(s.hi - s.lo).mean() * 1200:+5.1f}%/년 (t={newey_west_t(s.hi - s.lo):+5.2f})")
    print()

    # 최근 10년 롤링 (롱온리)
    roll = df['tilt'].rolling(120).mean() * 12
    print("■ 롱온리 초과수익의 10년 롤링 평균 (연)")
    for y in range(1940, df.index.max().year + 1, 10):
        p = pd.Period(f'{y}-12', freq='M')
        if p in roll.index and roll.loc[p] == roll.loc[p]:
            print(f"  {y}년 말 기준 직전 10년: {roll.loc[p] * 100:+.1f}%")
    last = roll.dropna().iloc[-1]
    print(f"  최근({df.index.max()}) 기준 직전 10년: {last * 100:+.1f}%")

    # 최악 구간
    g = (1 + df['tilt']).cumprod()
    dd = g / g.cummax() - 1
    print(f"\n■ 롱온리 초과수익의 최대 상대 낙폭: {dd.min() * 100:.1f}% (저점 {dd.idxmin()})")
    worst = df['tilt'].nsmallest(3)
    print("  최악의 달: " + ", ".join(f"{p} {v * 100:+.1f}%" for p, v in worst.items()))


if __name__ == '__main__':
    main()
