import time
import json
import requests
from curl_cffi import Session
from patchright.sync_api import sync_playwright

from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

import undetected_chromedriver as uc

http_session = Session(impersonate="chrome136")

# result = http_session.get(
#     # url="https://www.wildberries.by/catalog/40926567/detail.aspx"
#     url="https://megamarket.ru/catalog/details/smartfon-apple-iphone-14-pro-128gb-deep-purple-100055896761/"
# )
# print(result.content.decode("utf-8"))

with sync_playwright() as p:
    browser = p.chromium.launch_persistent_context(
        user_data_dir="",
        headless=False,
    )
    page = browser.new_page()
    page.goto('https://megamarket.ru/catalog/details/chereshnya-100045557655/')

    page.wait_for_selector("#stickyHeader")
    page_cookies = browser.cookies(urls=["https://megamarket.ru/"])

    print(page_cookies)
    page.screenshot(path=f'example-{p.chromium.name}.png')
    browser.close()


#
# options = Options()
# options.add_argument("--disable-gpu")
# options.add_argument("--no-sandbox")
#
# driver = uc.Chrome(headless=False)
# driver.implicitly_wait(15)
# driver.get('https://megamarket.ru/')
# time.sleep(15)
# driver.save_screenshot('nowsecure.png')

# import requests
#
# cookies = {
#     # '__Secure-ETC': '6bfb7521585ffbd13a54c92de4836787',
#     '__Secure-access-token': '8.0.Prm3FWJ6RTa7_O7mVytHmw.70.AUh5HL-xSJV9HIIR-ArWaFVz2St-R1MQCoYTlz_WN8w2_3oRg5fnmy74ybBokWfidXuq3jwzXXKaRCB6QhDEkiAqaDsJswqEv2C4tn3BvsxW..20250801145502.rxH08I3QuRz1Jn-uAQ8jxY8mX6cekFwwiJI14Cb_QVo.1fd014854248e41c1',
#     # '__Secure-refresh-token': '8.0.Prm3FWJ6RTa7_O7mVytHmw.70.AUh5HL-xSJV9HIIR-ArWaFVz2St-R1MQCoYTlz_WN8w2_3oRg5fnmy74ybBokWfidXuq3jwzXXKaRCB6QhDEkiAqaDsJswqEv2C4tn3BvsxW..20250801145502._z91nlTV6zq3ES70-KVuKffkbRWd-Wb63imy2PAUdAU.19499adbb7d32e0b7',
#     # '__Secure-ab-group': '70',
#     # '__Secure-user-id': '0',
#     # 'xcid': '075cbfca9840f72d568f066c9da94dae',
#     # '__Secure-ext_xcid': '075cbfca9840f72d568f066c9da94dae',
#     'abt_data': '7.aN1Z_8hD6L70P2bM38rLiJHQ5K7rLGhTQ5zmAyN9YL0GQeGLnOUw2Z-2AlUppffJVFD6aCDpClhy3fPWH3ce2ZZqUHC-vjDpqOtZ6AaDxsx7a0GnQsVm9TZWNyrlNZrJdZE_eNcryo4-prVRBfEDbyOdQyMgd7bZQYahvUrPe3MrnFPn7lDltldJCK2YyxW1IG6DOHKV7uG8VYN7rXbCjFW1nuchmK25Km1nPgNpIRUymhDjpPtJuZuQd7fmFpo76qwm27zC4djdmpd79FTL1E_OP17cdiYPmHKSfzyNZwI9ZhLzCfhm3boHJbt6hCNouiSc6fpjeT9RIjOLW7PCO89uICSoA1JDCK7mYfsFaCpfj5lgM52ajKwPa-LINZRgDzcQiYphMMYn4IMbU0De2AfUIrjAlNIo11aTIN3c8DZz7FqnTwX9P0OuSb6IO5aOwDjKJBN7uwtzORTWAMnA6NOqz8yj5g',
# }
#
# headers = {
#     'accept': 'application/json',
#     'accept-language': 'ru-RU,ru;q=0.9',
#     'cache-control': 'no-cache',
#     'content-type': 'application/json',
#     'pragma': 'no-cache',
#     'priority': 'u=1, i',
#     'referer': 'https://ozon.by/product/shumin-maska-dlya-sna-chernaya-1563221610/?__rr=1',
#     'sec-ch-ua': '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
#     'sec-ch-ua-mobile': '?0',
#     'sec-ch-ua-platform': '"Windows"',
#     'sec-fetch-dest': 'empty',
#     'sec-fetch-mode': 'cors',
#     'sec-fetch-site': 'same-origin',
#     'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
#     'x-kl-saas-ajax-request': 'Ajax_Request',
#     'x-o3-app-name': 'dweb_client',
#     'x-o3-app-version': 'release_31-6-2025_92ce224d',
#     'x-o3-manifest-version': 'frontend-ozon-ru:92ce224db940e1a7da3688dd12207ae24c7ef3d6,sf-render-api:100a986a9257dfdf22e7c029f43488da3e00e497',
#     'x-o3-parent-requestid': 'c69d7064f538fe7ae5a8d82541d53d04',
#     'x-page-view-id': 'a52820ef-2e7a-43b7-e2bc-993c5842b2f9',
#     # 'cookie': '__Secure-ETC=6bfb7521585ffbd13a54c92de4836787; __Secure-access-token=8.0.Prm3FWJ6RTa7_O7mVytHmw.70.AUh5HL-xSJV9HIIR-ArWaFVz2St-R1MQCoYTlz_WN8w2_3oRg5fnmy74ybBokWfidXuq3jwzXXKaRCB6QhDEkiAqaDsJswqEv2C4tn3BvsxW..20250801145502.rxH08I3QuRz1Jn-uAQ8jxY8mX6cekFwwiJI14Cb_QVo.1fd014854248e41c1; __Secure-refresh-token=8.0.Prm3FWJ6RTa7_O7mVytHmw.70.AUh5HL-xSJV9HIIR-ArWaFVz2St-R1MQCoYTlz_WN8w2_3oRg5fnmy74ybBokWfidXuq3jwzXXKaRCB6QhDEkiAqaDsJswqEv2C4tn3BvsxW..20250801145502._z91nlTV6zq3ES70-KVuKffkbRWd-Wb63imy2PAUdAU.19499adbb7d32e0b7; __Secure-ab-group=70; __Secure-user-id=0; xcid=075cbfca9840f72d568f066c9da94dae; __Secure-ext_xcid=075cbfca9840f72d568f066c9da94dae; abt_data=7.aN1Z_8hD6L70P2bM38rLiJHQ5K7rLGhTQ5zmAyN9YL0GQeGLnOUw2Z-2AlUppffJVFD6aCDpClhy3fPWH3ce2ZZqUHC-vjDpqOtZ6AaDxsx7a0GnQsVm9TZWNyrlNZrJdZE_eNcryo4-prVRBfEDbyOdQyMgd7bZQYahvUrPe3MrnFPn7lDltldJCK2YyxW1IG6DOHKV7uG8VYN7rXbCjFW1nuchmK25Km1nPgNpIRUymhDjpPtJuZuQd7fmFpo76qwm27zC4djdmpd79FTL1E_OP17cdiYPmHKSfzyNZwI9ZhLzCfhm3boHJbt6hCNouiSc6fpjeT9RIjOLW7PCO89uICSoA1JDCK7mYfsFaCpfj5lgM52ajKwPa-LINZRgDzcQiYphMMYn4IMbU0De2AfUIrjAlNIo11aTIN3c8DZz7FqnTwX9P0OuSb6IO5aOwDjKJBN7uwtzORTWAMnA6NOqz8yj5g',
# }
#
# params = {
#     'url': '/product/shumin-maska-dlya-sna-chernaya-1563221610/?__rr=1&layout_container=pdpPage2column&layout_page_index=2&sh=cSkxzZtTHw&start_page_id=c69d7064f538fe7ae5a8d82541d53d04',
# }
#
# response = http_session.get(
#     url='https://ozon.by/api/entrypoint-api.bx/page/json/v2',
#     params=params,
#     cookies=cookies,
#     headers=headers
# )
# result = response.json()
#
# print(result)


#
# headers = {
#     'Authorization': 'Bearer qnj46i40qynbml53elq0kjyda6qnv630nao5ti9s0ktw9pbim1liibwqjf4g5qwu',
#     'content-type': 'application/json',
# }
#
# json_data = {
#     'url': 'https://www.wildberries.by/catalog/40926567/detail.aspx',
#     'type': 'html',
#     'country': 'ru',
#     'js_render': 'True',
# }
#
# response = requests.post('https://unlocker-api.lunaproxy.com/request', headers=headers, json=json_data)
# result = response.content
# print(result)


# def get_cookies():
#     with uc.Chrome(service=ChromeService(ChromeDriverManager().install())) as driver:
#         driver.implicitly_wait(5)
#         driver.get("https://www.ozon.ru")
#         driver.find_element(By.CSS_SELECTOR, "#stickyHeader")
#         user_agent = driver.execute_script("return navigator.userAgent")
#         cookies = driver.get_cookies()
#
#     cookies_dict = {i["name"]:i["value"] for i in cookies}
#
#     return user_agent, cookies_dict
#
#
# user_agent, cookies_dict = get_cookies()
#
# print(f"User-Agent: {user_agent}")
# print(f"Cookies: {cookies_dict}")
#
# with open("cookies.txt", "w") as f:
#     json.dump(cookies_dict, f, indent=2)
