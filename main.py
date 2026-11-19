from datetime import datetime, timedelta, timezone
import json
import os
import random
import re
import urllib.parse
import urllib.request
import requests

# 從 GitHub Secrets 讀取設定
SIMBRIEF_USER = os.environ.get("SIMBRIEF_USER")
HOPPIE_LOGON = os.environ.get("HOPPIE_LOGON")
DISPATCH_CALLSIGN = "EVA FCD"
STATE_FILE = "flight_state.json"


def load_state():
  if os.path.exists(STATE_FILE):
    try:
      with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)
    except Exception:
      pass
  return {}


def save_state(state):
  with open(STATE_FILE, "w", encoding="utf-8") as f:
    json.dump(state, f, indent=2, ensure_ascii=False)


def get_simbrief_data(username):
  url = f"https://simbrief.com{username}&json=1"
  try:
    res = requests.get(url, timeout=10)
    if res.status_code == 200:
      return res.json()
  except Exception as e:
    print(f"取得 SimBrief 資料失敗: {e}")
  return None


def get_flight_real_gate(iata_flight, target_date_str, dest_icao):
  try:
    if dest_icao == "VHHH":
      url = f"https://hongkongairport.com{target_date_str}&lang=en&cargo=false&arrival=true"
      res = requests.get(url, timeout=10)
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
                return stand.strip()

    elif dest_icao == "RCTP":
      url = "https://transportdata.tw"
      headers = {
          "User-Agent": (
              "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
          ),
          "Accept": "application/json",
      }
      res = requests.get(url, headers=headers, timeout=10)
      if res.status_code == 200:
        data = res.json()
        flight_list = (
            data if isinstance(data, list) else data.get("Flights", [])
        )
        for item in flight_list:
          airline = item.get("AirlineID", "").strip()
          f_num = item.get("FlightNumber", "").strip()
          if f"{airline}{f_num}" == iata_flight or f_num == iata_flight:
            gate = item.get("Gate", "").strip()
            if gate:
              return gate
  except Exception as e:
    print(f"[{dest_icao} 查詢異常 {target_date_str}] {e}")
  return None


def get_fallback_gate(dest_icao):
  try:
    with open("gates.json", "r", encoding="utf-8") as f:
      gates_db = json.load(f)
    gate_list = gates_db.get(dest_icao) or gates_db.get("DEFAULT", ["B4"])
    return random.choice(gate_list)
  except Exception:
    return "B4"


def resolve_arrival_gate(dest_icao, icao_airline, flight_num):
  iata_prefix = "BR" if icao_airline == "EVA" else icao_airline
  iata_flight = f"{iata_prefix}{flight_num}"

  now_utc = datetime.now(timezone.utc)
  today_str = now_utc.strftime("%Y-%m-%d")
  yesterday_str = (now_utc - timedelta(days=1)).strftime("%Y-%m-%d")

  real_gate = get_flight_real_gate(iata_flight, today_str, dest_icao)
  if real_gate:
    return real_gate, "REAL-TIME (TODAY)"

  real_gate = get_flight_real_gate(iata_flight, yesterday_str, dest_icao)
  if real_gate:
    return real_gate, "REAL-TIME (YESTERDAY)"

  return get_fallback_gate(dest_icao), "OPS ASSIGNED (JSON)"


def parse_atot_to_timestamp(atot_hhmm, now_utc):
  """將 4 位數 UTC 時間 (如 '0715') 轉換為最接近現在的 UTC Timestamp"""
  hour = int(atot_hhmm[:2])
  minute = int(atot_hhmm[2:])
  if not (0 <= hour <= 23 and 0 <= minute <= 59):
    return None

  candidate = now_utc.replace(hour=hour, minute=minute, second=0, microsecond=0)
  # 處理跨日情況（例如現在 UTC 00:30，機師回報 23:45 起飛，應算作昨天）
  if candidate > now_utc + timedelta(hours=2):
    candidate -= timedelta(days=1)
  elif candidate < now_utc - timedelta(hours=22):
    candidate += timedelta(days=1)
  return int(candidate.timestamp())


def poll_hoppie_atot(logon, dispatch_callsign, target_callsign):
  """輪詢 Hoppie，尋找包含 ATOT HHMM 的訊息"""
  base_url = "http://hoppie.nl"
  params = {
      "logon": logon,
      "from": dispatch_callsign,
      "to": dispatch_callsign,
      "type": "poll",
  }
  full_url = f"{base_url}?{urllib.parse.urlencode(params)}"
  try:
    with urllib.request.urlopen(full_url, timeout=10) as response:
      raw_text = response.read().decode("utf-8").strip()
      if not raw_text.startswith("ok"):
        return None

      packets = re.findall(
          r"\{([A-Z0-9-]+)\s+([a-z]+)\s+\{(.*?)\}\}",
          raw_text,
          re.DOTALL | re.IGNORECASE,
      )
      for sender, _, content in packets:
        if sender.strip().upper() == target_callsign.upper():
          # 匹配 ATOT 0715、ATOT:0715Z、ATOT 07:15 等格式
          match = re.search(
              r"ATOT[:\s]*([0-2]\d)[:\s]?([0-5]\d)Z?", content.upper()
          )
          if match:
            return f"{match.group(1)}{match.group(2)}"
  except Exception as e:
    print(f"輪詢 Hoppie 失敗: {e}")
  return None


def send_hoppie_telex(logon, sender, recipient, message):
  base_url = "http://hoppie.nl"
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
      print(f"Hoppie 發送回應: {response.read().decode('utf-8')}")
      return True
  except Exception as e:
    print(f"發送 Hoppie 訊息失敗: {e}")
    return False


def main():
  data = get_simbrief_data(SIMBRIEF_USER)
  if not data:
    print("無法取得 SimBrief 資料")
    return

  general = data.get("general", {})
  times = data.get("times", {})

  flight_num = general.get("flight_number", "868")
  icao_airline = general.get("icao_airline", "EVA")
  callsign = f"{icao_airline}{flight_num}"
  dest = data.get("destination", {}).get("icao_code", "RCTP")

  # 取得 SimBrief 計畫生成時間作為這趟飛行的唯一識別 ID
  plan_id = str(data.get("params", {}).get("time_generated", callsign))
  # 取得預計空中飛行時間 (秒)，若無則取 block time
  ete_seconds = int(
      times.get("est_time_enroute") or times.get("est_block") or 0
  )

  now_utc = datetime.now(timezone.utc)
  now_ts = int(now_utc.timestamp())

  state = load_state()
  # 若 SimBrief 已經產生新的飛行計畫，重置舊狀態
  if state.get("plan_id") != plan_id:
    state = {
        "plan_id": plan_id,
        "callsign": callsign,
        "atot_ts": None,
        "gate_sent": False,
    }

  # 1. 檢查是否有新的 ATOT 電傳進來（允許起飛後更新）
  atot_hhmm = poll_hoppie_atot(HOPPIE_LOGON, DISPATCH_CALLSIGN, callsign)
  if atot_hhmm:
    atot_ts = parse_atot_to_timestamp(atot_hhmm, now_utc)
    if atot_ts:
      state["atot_ts"] = atot_ts
      state["atot_str"] = f"{atot_hhmm}Z"
      print(f"收到 {callsign} 實際起飛時間 ATOT: {atot_hhmm}Z")
      save_state(state)

  # 2. 檢查是否已記錄 ATOT 且尚未發送過 Gate 訊息
  if not state.get("atot_ts"):
    print(f"尚未收到 {callsign} 的 ATOT 回報，待機中。")
    save_state(state)
    return

  if state.get("gate_sent"):
    print(f"{callsign} 本趟已發送過 Gate 資訊，待機中。")
    return

  # 3. 計算實際 ETA = ATOT + ETE (Flight Time)
  actual_eta_ts = state["atot_ts"] + ete_seconds
  actual_eta_dt = datetime.fromtimestamp(actual_eta_ts, tz=timezone.utc)
  mins_to_eta = (actual_eta_ts - now_ts) / 60

  print(
      f"航班: {callsign} | ATOT: {state.get('atot_str')} | 推算 ETA:"
      f" {actual_eta_dt.strftime('%H:%MZ')} | 距離抵達: {mins_to_eta:.1f} 分鐘"
  )

  # 4. 在 ETA 前 60 分鐘或以內觸發發送（設定 -30 分鐘容錯避免降落前漏發）
  if -30 <= mins_to_eta <= 60:
    gate, source = resolve_arrival_gate(dest, icao_airline, flight_num)
    msg = (
        f"ARRIVAL GATE INFO ({source})\n"
        f"FLT: {callsign} DEST: {dest}\n"
        f"ATOT: {state.get('atot_str')} ETA: {actual_eta_dt.strftime('%H%MZ')}\n"
        f"ASSIGNED GATE: {gate}\n"
        f"SAFE LANDING."
    )
    if send_hoppie_telex(HOPPIE_LOGON, DISPATCH_CALLSIGN, callsign, msg):
      state["gate_sent"] = True
      save_state(state)
      print(f"已發送 ACARS 電傳！停機位: {gate}")
  else:
    print("尚未進入 ETA 前 60 分鐘範圍，持續監控中。")


if __name__ == "__main__":
  main()
