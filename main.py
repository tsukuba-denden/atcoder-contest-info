import requests
from bs4 import BeautifulSoup
import datetime
import json
import yaml
import re
import os
import logging
import time # time モジュールをインポート

# ロギングの設定
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# AtCoderのコンテストページURL
CONTEST_LIST_URL = "https://atcoder.jp/contests/"
# 結果を保存するファイル名
OUTPUT_JSON = "contests.json"
OUTPUT_YAML = "contests.yaml"

def parse_duration(duration_str):
    """
    コンテスト時間文字列 (HH:MM) を分単位の整数に変換する
    """
    try:
        hours, minutes = map(int, duration_str.split(':'))
        return hours * 60 + minutes
    except ValueError:
        logging.warning(f"Failed to parse duration: '{duration_str}'")
        return None

def parse_rated_range(rated_str):
    """
    Rated対象範囲文字列を解析し、整形して返す
    ハイフン形式にも対応
    """
    original_str = rated_str
    rated_str = rated_str.strip()
    logging.debug(f"Parsing rated_range input: '{original_str}' (stripped: '{rated_str}')")

    if not rated_str or rated_str == '-':
        logging.debug("Rated range string is empty or '-', returning empty string.")
        return ""

    if rated_str == "All":
        logging.debug("Rated range is 'All'.")
        return "All"

    match_tilde_upper = re.match(r"[~-]\s*(\d+)", rated_str)
    if match_tilde_upper:
        symbol = rated_str.lstrip()[0]
        result = f"{symbol} {match_tilde_upper.group(1)}"
        logging.debug(f"Matched '[~-] XXXX' format. Result: '{result}'")
        return result

    match_lower_upper = re.match(r"(\d+)\s*[~-]\s*(\d+)", rated_str)
    if match_lower_upper:
        match_symbol = re.search(r"\s([~-])\s", rated_str)
        symbol = match_symbol.group(1) if match_symbol else '~'
        result = f"{match_lower_upper.group(1)} {symbol} {match_lower_upper.group(2)}"
        logging.debug(f"Matched 'XXXX [~-] YYYY' format. Result: '{result}'")
        return result

    match_lower_tilde = re.match(r"(\d+)\s*[~-]", rated_str)
    if match_lower_tilde:
        symbol = rated_str.rstrip()[-1]
        result = f"{match_lower_tilde.group(1)} {symbol}"
        logging.debug(f"Matched 'XXXX [~-]' format. Result: '{result}'")
        return result

    logging.warning(f"Unknown rated range format encountered: '{rated_str}'. Returning original stripped string.")
    return rated_str

def fetch_and_parse_page(url, language='ja'):
    """指定された言語設定でページを取得し、BeautifulSoupオブジェクトを返す"""
    headers = {
        'Accept-Language': f'{language},en-US;q=0.8,en;q=0.7' if language == 'ja' else 'en-US,en;q=0.9,ja;q=0.8'
    }
    logging.info(f"Fetching contest page with language '{language}'...")
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        response.encoding = response.apparent_encoding
        logging.info(f"Successfully fetched contest page with language '{language}'.")
        return BeautifulSoup(response.text, 'html.parser')
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching contest page with language '{language}': {e}")
        return None

def extract_contest_names(soup, language):
    """BeautifulSoupオブジェクトからコンテストURLをキー、コンテスト名を値とする辞書を抽出"""
    names = {}
    if not soup:
        return names

    tables = [soup.find(id="contest-table-upcoming"), soup.find(id="contest-table-recent")]
    for table in tables:
        if not table:
            continue
        tbody = table.find('tbody')
        if not tbody:
            continue
        for row in tbody.find_all('tr'):
            cols = row.find_all('td')
            if len(cols) < 2:
                continue
            try:
                link_tag = cols[1].find('a')
                if link_tag and 'href' in link_tag.attrs:
                    url = "https://atcoder.jp" + link_tag['href']
                    name = link_tag.text.strip()
                    names[url] = name
                    logging.debug(f"[{language}] Found name: '{name}' for URL: {url}")
            except Exception as e:
                logging.warning(f"Error extracting name for language '{language}': {e} in row: {row.text.strip()}")
    return names


def scrape_atcoder_contests():
    """
    AtCoderのコンテストページをスクレイピングしてコンテスト情報を取得する
    日本語名と英語名の両方を取得 (1秒待機あり)
    rated_rangeが空の場合は '-' に変換
    """
    logging.info("Starting AtCoder contest scraping for both Japanese and English names...")

    # 1. 日本語ページを取得・パース
    soup_ja = fetch_and_parse_page(CONTEST_LIST_URL, language='ja')
    if not soup_ja:
        return [] # 日本語ページ取得失敗時は終了

    # --- 1秒待機 ---
    logging.info("Waiting 1 second before fetching English page...")
    time.sleep(1)
    # ---------------

    # 2. 英語ページを取得・パース
    soup_en = fetch_and_parse_page(CONTEST_LIST_URL, language='en')
    # 英語ページ取得失敗は致命的ではないので続行

    # 3. 英語のコンテスト名を抽出 (URL: Name_EN の辞書)
    english_names = extract_contest_names(soup_en, 'en')
    logging.info(f"Extracted {len(english_names)} English contest names.")

    # 4. 日本語ページから基本情報をパースし、英語名をマージ
    contests = []
    upcoming_contests_table = soup_ja.find(id="contest-table-upcoming")
    recent_contests_table = soup_ja.find(id="contest-table-recent")

    tables_to_parse = []
    if upcoming_contests_table:
        tables_to_parse.append(("Upcoming", upcoming_contests_table.find('tbody')))
        logging.info("Found upcoming contests table (ja).")
    else:
        logging.warning("Upcoming contests table not found (ja).")
    if recent_contests_table:
        tables_to_parse.append(("Recent", recent_contests_table.find('tbody')))
        logging.info("Found recent contests table (ja).")
    else:
        logging.warning("Recent contests table not found (ja).")

    if not tables_to_parse:
        logging.error("Could not find any contest tables (ja).")
        return []

    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))) # JST
    logging.info(f"Current JST time: {now.isoformat()}")

    contest_count = 0
    for table_type, tbody in tables_to_parse:
        if not tbody:
            logging.warning(f"tbody not found for {table_type} table (ja).")
            continue
        logging.info(f"Parsing {table_type} contests (ja)...")
        for i, row in enumerate(tbody.find_all('tr')):
            cols = row.find_all('td')
            logging.debug(f"Processing row {i+1} in {table_type} table (ja). Columns found: {len(cols)}")
            if len(cols) < 4:
                logging.warning(f"Skipping row {i+1} in {table_type} (ja): Expected at least 4 columns, found {len(cols)}. Row data: {row.text.strip()}")
                continue

            try:
                # --- 開始日時 ---
                start_time_tag = cols[0].find('time')
                if not start_time_tag or not start_time_tag.text:
                    logging.warning(f"Skipping row {i+1} in {table_type} (ja): Start time tag or text not found.")
                    continue
                start_time_str = start_time_tag.text.strip()
                start_time = datetime.datetime.fromisoformat(start_time_str)

                # --- コンテスト名(日本語)とリンク ---
                contest_link_tag = cols[1].find('a')
                if not contest_link_tag or not contest_link_tag.text or 'href' not in contest_link_tag.attrs:
                    logging.warning(f"Skipping row {i+1} in {table_type} (ja): Contest link tag, text or href not found.")
                    continue
                contest_name_ja = contest_link_tag.text.strip()
                contest_url = "https://atcoder.jp" + contest_link_tag['href']

                # --- コンテスト名(英語) ---
                contest_name_en = english_names.get(contest_url, "")
                if not contest_name_en:
                     logging.warning(f"English name not found for URL: {contest_url}")


                # --- コンテスト時間 ---
                duration_str = cols[2].text.strip()
                duration_min = parse_duration(duration_str)

                # --- Rated対象範囲 ---
                rated_range_str_raw = cols[3].text
                rated_range = parse_rated_range(rated_range_str_raw)

                # --- ステータス判定 ---
                status = "Upcoming" if start_time > now else "Recent"

                # --- contest_info 辞書の作成 ---
                contest_info = {
                    "name_ja": contest_name_ja,
                    "name_en": contest_name_en,
                    "url": contest_url,
                    "start_time": start_time.isoformat(),
                    "duration_min": duration_min,
                    # rated_rangeが空文字列""の場合に"-"に変換する
                    "rated_range": rated_range if rated_range else "-",
                    "status": status
                }
                contests.append(contest_info)
                contest_count += 1
                logging.debug(f"  Added contest: {contest_name_ja} / {contest_name_en}")

            except Exception as e:
                logging.error(f"Error parsing row {i+1} in {table_type} (ja): {e}", exc_info=True)
                logging.error(f"  Row data that caused error: {row.prettify()}")
                continue

    logging.info(f"Finished parsing. Total contests found: {contest_count}")

    # 開始時間でソート (新しい順)
    contests.sort(key=lambda x: x["start_time"], reverse=True)
    logging.info("Contests sorted by start time (descending).")

    return contests

def save_contests_to_json(contests, filename):
    """
    コンテスト情報をJSONファイルに保存する
    """
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        filepath = os.path.join(script_dir, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(contests, f, ensure_ascii=False, indent=4)
        logging.info(f"Successfully saved {len(contests)} contests to {filepath}")
    except IOError as e:
        logging.error(f"Error saving contests to JSON: {e}")
    except Exception as e:
        logging.error(f"An unexpected error occurred during JSON saving: {e}", exc_info=True)

def save_contests_to_yaml(contests, filename):
    """
    コンテスト情報をYAMLファイルに保存する
    """
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        filepath = os.path.join(script_dir, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            class NullRepresenter:
                def represent_none(self, data):
                    return self.represent_scalar('tag:yaml.org,2002:null', '')
            yaml.add_representer(type(None), NullRepresenter.represent_none)

            yaml.dump(contests, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        logging.info(f"Successfully saved {len(contests)} contests to {filepath}")
    except IOError as e:
        logging.error(f"Error saving contests to YAML: {e}")
    except Exception as e:
        logging.error(f"An unexpected error occurred during YAML saving: {e}", exc_info=True)


if __name__ == "__main__":
    # デバッグレベルのログも表示する場合
    # logging.getLogger().setLevel(logging.DEBUG)

    logging.info("Script execution started.")
    contest_data = scrape_atcoder_contests()
    if contest_data:
        logging.info(f"Scraped {len(contest_data)} contests. Saving to files...")
        save_contests_to_json(contest_data, OUTPUT_JSON)
        save_contests_to_yaml(contest_data, OUTPUT_YAML)
    else:
        logging.warning("No contest data scraped.")
    logging.info("Script execution finished.")
