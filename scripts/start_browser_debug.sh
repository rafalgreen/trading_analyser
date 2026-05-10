#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-9222}"

if ! [[ "$PORT" =~ ^[0-9]+$ ]] || [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
  echo "Nieprawidłowy port: $PORT"
  echo "Użycie: scripts/start_browser_debug.sh [port]"
  exit 2
fi

if command -v lsof >/dev/null 2>&1; then
  if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port $PORT jest już zajęty (coś już nasłuchuje)."
    echo "Sprawdź: lsof -nP -iTCP:$PORT -sTCP:LISTEN"
    exit 3
  fi
fi

launch_browser() {
  local app_name="$1"
  local app_path="$2"
  if [ -d "$app_path" ]; then
    # -n = nowa instancja, -a = konkretna aplikacja
    open -na "$app_name" --args --remote-debugging-port="$PORT" >/dev/null 2>&1 || true
    return 0
  fi
  return 1
}

echo "Uruchamiam przeglądarkę z CDP na porcie $PORT…"

if launch_browser "Brave Browser" "/Applications/Brave Browser.app"; then
  echo "OK: Brave uruchomiony."
elif launch_browser "Google Chrome" "/Applications/Google Chrome.app"; then
  echo "OK: Chrome uruchomiony."
else
  echo "Nie znaleziono Brave ani Chrome w /Applications."
  echo "Ręcznie uruchom:"
  echo "  /Applications/Brave\\ Browser.app/Contents/MacOS/Brave\\ Browser --remote-debugging-port=$PORT"
  echo "albo:"
  echo "  /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome --remote-debugging-port=$PORT"
  exit 4
fi

echo "Czekam aż CDP zacznie nasłuchiwać…"
for _ in {1..30}; do
  if curl -fsS "http://127.0.0.1:${PORT}/json/version" >/dev/null 2>&1; then
    echo "CDP działa: http://127.0.0.1:${PORT}"
    echo "Otwórz w tej sesji kartę z wykresem TradingView."
    exit 0
  fi
  sleep 0.2
done

echo "Nie udało się potwierdzić CDP na http://127.0.0.1:${PORT}."
echo "Jeśli przeglądarka się uruchomiła, poczekaj chwilę i sprawdź ręcznie:"
echo "  curl -sS http://127.0.0.1:${PORT}/json/version | head -c 200"
exit 5

