# Git 작업 가이드 — 왜 계속 충돌이 나는가

## 근본 원인

이 저장소에는 `.github/workflows/daily_scan.yml`이 있고, **평일 매일 06:30 KST에
GitHub Actions가 원격 `main`에 직접 커밋을 push**한다.

```yaml
- cron: '30 21 * * 1-5'   # UTC 21:30 = KST 06:30
...
git add daily_recommendations.json recommendation_history.json
git commit -m "chore(auto): Update daily recommendations and history [skip ci]"
git push
```

즉 내가 아무것도 안 해도 원격 `main`이 매일 앞서간다. 로컬에서 하루 이상 작업하다
push하면 거의 항상 다음 순서로 막힌다.

```
push 거부 → pull 필요 → 분기 갈라짐 → merge 전략 미지정 → 충돌 → 미완료 병합
```

충돌 파일은 대부분 `daily_recommendations.json`과 `recommendation_history.json`이다.
**둘 다 스크립트가 생성하는 파일이므로 손으로 병합할 이유가 없다.**

---

## 1회만 하면 되는 설정

```bash
cd ~/path/to/make-own-money

# (1) pull 전략 고정 — 매번 묻지 않게 한다
git config pull.rebase true          # 권장: 내 커밋을 원격 위로 쌓음 (이력이 깔끔)
# git config pull.rebase false       # 대안: 머지 커밋 생성

# (2) 생성 파일은 충돌 시 항상 원격 버전을 채택하는 병합 드라이버 등록
git config merge.keep-remote.name "generated file: keep incoming version"
git config merge.keep-remote.driver 'cp -f "%B" "%A"'
```

`.gitattributes`는 저장소에 포함되어 있어 두 JSON 파일을 이 드라이버로 연결한다.
따라서 (2)만 로컬에 한 번 등록하면 그 두 파일은 다시는 충돌로 멈추지 않는다.

> `%B`는 병합해 들어오는 쪽, `%A`는 결과 파일이다. `git pull --no-rebase`(머지)에서는
> 원격(봇) 버전이 `%B`이므로 원격 것이 남는다. `git pull --rebase`에서는 ours/theirs가
> 뒤집혀 내 커밋의 버전이 남는다. 어느 쪽이든 **충돌로 멈추지 않는 것**이 목적이고,
> 생성 파일이므로 다음 스캔이 어차피 다시 덮어쓴다.

---

## 2. 평소 작업 루틴

```bash
# 작업 시작 전 — 항상 먼저 당긴다
git pull origin main

# 작업 후
git add -A
git commit -m "..."
git pull origin main        # 그 사이 봇이 올린 커밋 흡수
git push origin main
```

`pull.rebase true`로 설정했다면 `git pull`이 알아서 rebase한다.

---

## 3. 겪었던 문제별 대처

### 1️⃣ 패치 파일을 못 찾음

```
error: could not open 'quant-accuracy-fix.patch': No such file or directory
```

`git am`은 **현재 디렉터리 기준**으로 파일을 찾는다. 다운로드 폴더에 있으면
경로를 다 적거나 옮긴 뒤 실행한다.

```bash
cd ~/path/to/make-own-money
git am ~/Downloads/quant-accuracy-fix.patch     # 경로 지정
# 또는
mv ~/Downloads/quant-accuracy-fix.patch .
git am quant-accuracy-fix.patch
```

적용 전에 내용을 먼저 확인하고 싶으면:

```bash
git apply --check ~/Downloads/quant-accuracy-fix.patch   # 적용 가능 여부만 검사
git apply --stat  ~/Downloads/quant-accuracy-fix.patch   # 변경 파일 목록
```

`git am`이 중간에 실패하면 반드시 상태를 정리하고 다시 시작한다.

```bash
git am --abort
```

### 2️⃣ Push 거부 (원격에 로컬에 없는 커밋)

```
! [rejected]  main -> main (fetch first)
hint: Updates were rejected because the remote contains work that you do not have locally.
```

봇이 올린 커밋이 원격에 있다는 뜻이다. **절대 `git push -f`로 밀지 말 것** —
봇이 기록한 추천 이력이 날아간다. 그냥 당겨서 합친다.

```bash
git pull origin main
git push origin main
```

### 3️⃣ 분기 갈라짐 (merge 전략 미지정)

```
hint: You have divergent branches and need to specify how to reconcile them.
fatal: Need to specify how to reconcile divergent branches.
```

위 "1회 설정"의 `git config pull.rebase`를 안 했을 때 나온다. 그 자리에서 해결하려면:

```bash
git pull origin main --rebase      # 권장
# 또는
git pull origin main --no-rebase   # 머지 커밋 생성
```

### 4️⃣ 미완료 병합 (MERGE_HEAD 존재)

```
error: You have not concluded your merge (MERGE_HEAD exists).
```

충돌이 난 병합이 끝나지 않은 상태다. 두 갈래 중 하나를 택한다.

**(a) 병합을 마무리한다** — 충돌을 먼저 해결해야 한다.

```bash
git status                      # 충돌 파일 확인
# 생성 JSON 파일이라면 원격 것을 그대로 채택
git checkout --theirs daily_recommendations.json recommendation_history.json
git add daily_recommendations.json recommendation_history.json
# 소스 파일 충돌이라면 에디터로 <<<<<<< ======= >>>>>>> 표시를 직접 정리
git commit                      # 메시지는 기본값 그대로 두면 됨
```

**(b) 병합을 취소하고 처음부터 다시 한다**

```bash
git merge --abort               # 병합 시작 전 상태로 완전 복구
```

rebase 도중이라면 `git rebase --abort`를 쓴다.

---

## 4. 막히면 쓰는 안전 탈출구

무슨 상태인지 모르겠을 때:

```bash
git status                      # 지금 어떤 작업 중인지 알려준다
git log --oneline --graph -10   # 로컬 이력
git log --oneline origin/main -5  # 원격 이력 (git fetch 후)
```

작업 내용을 잃고 싶지 않다면 병합/리베이스를 건드리기 전에 백업 브랜치를 만든다.

```bash
git branch backup-$(date +%Y%m%d-%H%M)
```

되돌려야 할 때는 `git reflog`에 모든 이동 기록이 남아 있다.

```bash
git reflog                      # 예: HEAD@{3} 시점으로 돌아가고 싶다면
git reset --hard HEAD@{3}
```

---

## 5. 구조적으로 줄이는 방법 (선택)

충돌의 원인이 "생성 파일을 git으로 관리하는 것"이므로, 근본 해결책은 두 가지다.

1. **Supabase를 정식 저장소로 쓰고 JSON을 `.gitignore`에 넣는다.**
   Streamlit Cloud 로컬 폴백이 필요 없어지는 대신 Supabase 연결이 필수가 된다.
2. **봇 커밋을 별도 브랜치(`data`)로 보낸다.**
   `daily_scan.yml`의 push 대상을 바꾸고, 앱은 그 브랜치의 파일을 읽는다.
   `main`은 소스만 담게 되어 충돌이 사라진다.

지금은 위 `.gitattributes` 병합 드라이버로 충분하지만, 협업자가 늘면 2번을 권한다.
