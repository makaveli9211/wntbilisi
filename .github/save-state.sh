#!/usr/bin/env bash
# Сохраняет subscribers.json в репозиторий.
#
# Список подписчиков — генерируемый файл, текстовое слияние для него
# бессмысленно. Поэтому: забираем версию из репозитория, объединяем списки
# по смыслу (bot.py --merge) и кладём результат ровно поверх удалённой ветки.
# Конфликт при таком порядке невозможен.
set -e

MESSAGE="${1:-Обновление списка подписчиков}"

if ! git ls-files --error-unmatch subscribers.json >/dev/null 2>&1; then
  echo "ОШИБКА: subscribers.json не отслеживается git — состояние не сохранится"
  exit 1
fi

if git diff --quiet -- subscribers.json; then
  echo "изменений нет"
  exit 0
fi

git config user.name  "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

for attempt in 1 2 3; do
  git fetch origin main
  git show origin/main:subscribers.json > /tmp/remote.json 2>/dev/null \
    || echo '{}' > /tmp/remote.json
  python bot.py --merge /tmp/remote.json

  git reset --mixed origin/main >/dev/null
  git add subscribers.json
  if git diff --cached --quiet; then
    # Наши изменения уже есть в origin/main — значит состояние записано.
    # Это нормальный исход, а не сбой.
    echo "после слияния коммитить нечего — состояние уже в репозитории"
    exit 0
  fi
  git commit -m "$MESSAGE"

  if git push origin HEAD:main; then
    exit 0
  fi
  echo "push не прошёл (попытка $attempt), пробую ещё раз"
  sleep 5
done

echo "не удалось сохранить состояние после трёх попыток"
exit 1
