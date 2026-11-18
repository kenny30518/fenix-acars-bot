from datetime import datetime, timezone
import os
import urllib.parse
import urllib.request
import requests

SIMBRIEF_USER = os.environ.get("SIMBRIEF_USER")
HOPPIE_LOGON = os.environ.get("HOPPIE_LOGON")
DISPATCH_CALLSIGN = "EVA"  # 發送方公司代號


def get_simbrief_data(username):
  url = f"https://www.simbrief.com/api/xml.fetcher.php?userid={username}&json=1"
  res = requests.get(url)
  return res.json() if res.status_code == 200 else None


def send_hoppie_telex(logon, sender, recipient, message):
  base_url = "http://www.hoppie.nl/acars/system/connect.html"
  params = {
      "logon": logon,
      "from": sender,
      "to": recipient,
      "type": "telex",
      "packet": message,
  }
  full_url = f"{base_url}?{urllib.parse.urlencode(params)}"
  with urllib.request.urlopen(full_url) as response:
    print(f"Hoppie 回應: {response.read().decode('utf-8')}")


def main():
  data = get_simbrief_data(SIMBRIEF_USER)
  if not data:
    print("無法取得 SimBrief 資料")
    return

  # 提取航班資訊與預計時間 (Unix Timestamp)
  general = data.get("general", {})
  times = data.get("times", {})

  flight_num = general.get("flight_number", "868")
  icao_airline = general.get("icao_airline", "EVA")
  callsign = f"{icao_airline}{flight_num}"
  dest = data.get("destination", {}).get("icao_code", "RCTP")

  # 取得 SimBrief 裡的預計停機位（若無則預設 B4，可自行調整）
  gate = data.get("destination", {}).get("gate") or "B4"

  # 計算距離預計降落 (est_landing) 還有幾分鐘
  est_landing = int(times.get("est_landing", 0))
  now_ts = int(datetime.now(timezone.utc).timestamp())
  mins_to_landing = (est_landing - now_ts) / 60

  print(
      f"航班: {callsign} | 目的地: {dest} | 距離預計降落: {mins_to_landing:.1f}"
      " 分鐘"
  )

  # 觸發條件：距離預計降落剩下 55 到 65 分鐘之間（配合每 5 分鐘檢查一次，避免重複發送）
  #if 55 <= mins_to_landing <= 60:
  #  msg = (
  #      f"ARRIVAL GATE INFO\nFLT: {callsign}\nDEST: {dest}\nASSIGNED GATE:"
  #     f" {gate}\nSAFE LANDING."
  #  )
  #  send_hoppie_telex(HOPPIE_LOGON, DISPATCH_CALLSIGN, callsign, msg)
  #  print("已成功發送 ACARS 停機位電傳！")
  #else:
  #  print("尚未到達發送時機，待機中。")

  if True:  # 測試用：強制立即發送
    msg = (
        f"TEST ARRIVAL GATE INFO\nFLT: {callsign}\nDEST: {dest}\nASSIGNED GATE:"
        f" {gate}\nETA MINS: {mins_to_landing:.1f}"
    )
    send_hoppie_telex(HOPPIE_LOGON, DISPATCH_CALLSIGN, callsign, msg)
    print("測試訊息已成功發送至 Hoppie！")

if __name__ == "__main__":
  main()
