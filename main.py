from datetime import datetime, timedelta, timezone
import json
import os
import random
import urllib.parse
import urllib.request
import requests

# 從 GitHub Secrets 讀取設定
SIMBRIEF_USER = os.environ.get("SIMBRIEF_USER")
HOPPIE_LOGON = os.environ.get("HOPPIE_LOGON")
DISPATCH_CALLSIGN = "EVA"  # 發送方公司代號


def get_simbrief_data(username):
  """抓取 SimBrief 飛行計畫 JSON 資料"""
  url = f"https://www.simbrief.com/api/xml.fetcher.php?username={username}&json=1"
  try:
    res = requests.get(url, timeout=10)
    if res.status_code == 200:
      return res.json()
  except Exception as e:
    print(f"取得 SimBrief 資料失敗: {e}")
  return None


def get_flight_real_gate(iata_flight, target_date_str, dest_icao):
  """依 SimBrief 換算出來的目標降落日期 (YYYY-MM-DD) 與機場查詢真實停機位"""
  try:
    if dest_icao == "VHHH":
      url = f"https://www.hongkongairport.com/flightinfo-rest/rest/flights/past?date={target_date_str}&lang=en&cargo=false&arrival=true"
      res = requests.get(url, timeout=10)
      print(f"[VHHH API] 狀態碼: {res.status_code}, 目標日期: {target_date_str}")
      if res.status_code == 200:
        for day in res.json():
          for flight_entry in day.get("list", []):
            flights = [
                f.get("no", "").replace(" ", "")
                for f in flight_entry.get("flight", [])
            ]
            if iata_flight in flights:
              stand = flight_entry.get("stand")
              if stand and stand.strip():
                print(f"找到 VHHH 真實停機位: {stand.strip()}")
                return stand.strip()

    elif dest_icao == "RCTP":
      url = "https://tdx.transportdata.tw/api/basic/v2/Air/FIDS/Airport/Arrival/RCTP"
      headers = {
          "User-Agent": (
              "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
          ),
          "Accept": "application/json",
      }
      res = requests.get(url, headers=headers, timeout=10)
      print(f"[RCTP TDX API] 狀態碼: {res.status_code}, 目標比對日期: {target_date_str}")

      if res.status_code == 200:
        data = res.json()
        flight_list = (
            data if isinstance(data, list) else data.get("Flights", [])
        )
        for item in flight_list:
          airline = item.get("AirlineID", "").strip()
          f_num = item.get("FlightNumber", "").strip()

          # 1. 班號必須相符
          if f"{airline}{f_num}" == iata_flight or f_num == iata_flight:
            # 2. 抓取預定降落時間字串
            arr_time = item.get(
                "ScheduleArrivalTime", item.get("ActualArrivalTime", "")
            )

            # 3. 嚴格比對：該航班的降落日期必須吻合 SimBrief 計算出的目標日期
            if target_date_str in arr_time:
              gate = item.get("Gate", "").strip()
              print(
                  f"找到相符航班: {airline}{f_num}, 時間: {arr_time}, 登機門:"
                  f" {gate or '尚未指派'}"
              )
              if gate:
                return gate
            else:
              print(
                  f"找到班號 {airline}{f_num} 但時間 ({arr_time}) 不符合目標日期"
                  f" {target_date_str}，略過。"
              )
  except Exception as e:
    print(f"[{dest_icao} 查詢異常 {target_date_str}] {e}")

  return None


def get_fallback_gate(dest_icao):
  """從 gates.json 讀取備援機位"""
  try:
    with open("gates.json", "r", encoding="utf-8") as f:
      gates_db = json.load(f)
    gate_list = gates_db.get(dest_icao) or gates_db.get("DEFAULT", ["B4"])
    chosen = random.choice(gate_list)
    return chosen
  except Exception as e:
    print(f"讀取 gates.json 失敗: {e}")
    return "B4"


def resolve_arrival_gate(dest_icao, icao_airline, flight_num, est_landing_ts):
  """三層防護查詢邏輯：以 SimBrief ETA 推算日期

  1. SimBrief ETA 當天 2. SimBrief ETA 前一天 (昨天) 3. gates.json 備援
  """
  iata_prefix = "BR" if icao_airline == "EVA" else icao_airline
  iata_flight = f"{iata_prefix}{flight_num}"

  # 關鍵：利用 SimBrief 的 est_landing 時間戳記，轉換成台灣時區 (UTC+8) 的降落日期
  taiwan_tz = timezone(timedelta(hours=8))
  landing_dt = datetime.fromtimestamp(est_landing_ts, taiwan_tz)

  target_date_str = landing_dt.strftime("%Y-%m-%d")
  yesterday_date_str = (landing_dt - timedelta(days=1)).strftime("%Y-%m-%d")

  # 1. 嘗試查詢 SimBrief ETA 當天的 FIDS 資料
  print(f"正在查詢【ETA 當天 ({target_date_str})】真實 FIDS 資料...")
  real_gate = get_flight_real_gate(iata_flight, target_date_str, dest_icao)
  if real_gate:
    print(f"成功取得當天真實機位: {real_gate}")
    return real_gate, f"REAL-TIME ({target_date_str})"

  # 2. 如果當天沒有，嘗試查詢 ETA 前一天（防範跨夜航班或排程落差）
  print(f"當天無資料，改為查詢【前一天 ({yesterday_date_str})】歷史資料...")
  real_gate = get_flight_real_gate(iata_flight, yesterday_date_str, dest_icao)
  if real_gate:
    print(f"成功取得前一天歷史機位: {real_gate}")
    return real_gate, f"REAL-TIME ({yesterday_date_str})"

  # 3. 兩者皆無，最後才使用 gates.json 備援
  fallback = get_fallback_gate(dest_icao)
  print(f"皆無法取得真實資料，使用 JSON 備援機位: {fallback}")
  return fallback, "OPS ASSIGNED (JSON)"


def send_hoppie_telex(logon, sender, recipient, message):
  """透過 Hoppie ACARS 傳送 telex 訊息"""
  base_url = "http://www.hoppie.nl/acars/system/connect.html"
  params = {
      "logon": logon,
      "from": sender,
      "to": recipient,
      "type": "telex",
      "packet": message,
  }
  full_url = f"{base_url}?{urllib.parse.urlencode(params)}"
  try:
    with urllib.request.urlopen(full_url, timeout=10) as response:
      print(f"Hoppie 回應: {response.read().decode('utf-8')}")
  except Exception as e:
    print(f"發送 Hoppie 訊息失敗: {e}")


def main():
  data = get_simbrief_data(SIMBRIEF_USER)
  if not data:
    print("無法取得 SimBrief 資料")
    return

  general = data.get("general", {})
  times = data.get("times", {})

  flight_num = general.get("flight_number", "870")
  icao_airline = general.get("icao_airline", "EVA")
  callsign = f"{icao_airline}{flight_num}"
  dest = data.get("destination", {}).get("icao_code", "RCTP")

  est_landing = int(times.get("est_landing", 0))
  now_ts = int(datetime.now(timezone.utc).timestamp())
  mins_to_landing = (est_landing - now_ts) / 60

  print(
      f"航班: {callsign} | 目的地: {dest} | 預計降落時間戳: {est_landing} |"
      f" 距離預計降落: {mins_to_landing:.1f} 分鐘"
  )

  # ==========================================
  # 【測試開關說明】
  # 測試時請確保維持 if True:
  # 正式上線自動運行時：改成 if 55 <= mins_to_landing <= 60:
  # ==========================================
  if True:
    gate, source = resolve_arrival_gate(
        dest, icao_airline, flight_num, est_landing
    )
    msg = (
        f"ARRIVAL GATE INFO ({source})\n"
        f"FLT: {callsign}\n"
        f"DEST: {dest}\n"
        f"ASSIGNED GATE: {gate}\n"
        f"SAFE LANDING."
    )
    send_hoppie_telex(HOPPIE_LOGON, DISPATCH_CALLSIGN, callsign, msg)
    print(f"已發送 ACARS 電傳！停機位: {gate} (來源: {source})")
  else:
    print("尚未到達發送時機（距離降落大約 1 小時），待機中。")


if __name__ == "__main__":
  main()
