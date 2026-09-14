"""
장부 앱 자동 회귀 테스트
========================

목적
----
단일 HTML 파일 구조 특성상 한 화면의 수정이 다른 화면에 영향을 줄 수 있어,
기능 추가·수정 시마다 전체 시나리오를 재검증하기 위해 작성.

검증 원칙
--------
1. 금액은 화면 표시 여부가 아니라 "직접 계산한 기대값"과 대조한다.
   → 사용자가 대조할 원본 장부가 없어, 틀린 숫자를 발견할 수단이 없기 때문
2. 화면 표시는 스크린샷이 아니라 렌더링 결과를 실측한다.
   → 지정값과 실제 적용값이 다른 결함은 육안으로 탐지되지 않음
3. 저장 결과는 UI가 아니라 localStorage 원본을 직접 확인한다.
   → 화면에만 반영되고 저장은 안 되는 경우를 걸러내기 위함

실행
----
    pip install playwright && playwright install chromium
    python test_regression.py
"""

from playwright.sync_api import sync_playwright
import pathlib

APP_PATH = pathlib.Path("app.html").resolve()
APP_URL = f"file://{APP_PATH}"

# ---------------------------------------------------------------------------
# 테스트 픽스처 — 케이스마다 동일한 초기 상태에서 시작하도록 고정
# ---------------------------------------------------------------------------

재료_배추 = {
    "id": 1, "name": "배추", "unitPrice": 10, "unitType": "weight",
    "deleted": False, "deletedAt": None,
    "history": [{"date": "2026-09-01", "price": 10000, "weight": 1000, "unitPrice": 10}],
}

메뉴_김치찌개 = {
    "id": 1, "name": "김치찌개", "sellPrice": 6000, "makeQty": 10,
    "recipe": [{"ingId": 1, "weight": 200}], "needsSetup": False, "editHistory": [],
}

# 현금 5건 + 카드 2건 = 7건 판매, 단가 6,000원 / 원가 1,600원
판매기록_9월4일 = {
    "2026-9-4": {"1": {
        "qty": 0, "cardQty": 2, "cashQty": 5, "cardFeeRate": 1.5,
        "unitCost": 1600, "sellPrice": 6000, "name": "김치찌개",
    }}
}

비용항목_전체 = {
    "utilities": True, "rent": True, "payroll": True,
    "depreciation": True, "lossRate": True, "interior": False, "supplies": False,
}


class TestRunner:
    def __init__(self, browser):
        self.browser = browser
        self.results = []
        self.js_errors = []

    def check(self, case_id, name, passed, actual=""):
        self.results.append((case_id, name, passed))
        mark = "PASS" if passed else "FAIL"
        print(f"[{mark}] {case_id} {name}" + (f"  | 실제값: {actual}" if actual else ""))

    def open_app(self, data=None):
        """앱을 열고 지정한 초기 데이터를 주입한다."""
        page = self.browser.new_page(viewport={"width": 420, "height": 900})
        page.on("pageerror", lambda e: self.js_errors.append(str(e)))
        page.on("dialog", lambda d: d.accept())
        page.goto(APP_URL)
        if data is not None:
            page.evaluate("(d) => localStorage.setItem('seniorLedgerV7', JSON.stringify(d))", data)
        page.reload()
        page.wait_for_timeout(400)
        return page

    @staticmethod
    def stored(page):
        """localStorage에 실제로 저장된 원본 데이터를 반환한다."""
        return page.evaluate("() => JSON.parse(localStorage.getItem('seniorLedgerV7'))")

    def summary(self):
        passed = sum(1 for _, _, ok in self.results if ok)
        print("\n" + "=" * 60)
        print(f"결과: {passed}/{len(self.results)} 통과")
        print(f"JS 런타임 에러: {len(self.js_errors)}건")
        for e in self.js_errors:
            print("  -", e)
        return passed == len(self.results) and not self.js_errors


# ===========================================================================
# TC-A. 계산 정확도  [위험도: 높음]
#   근거: 계산이 틀려도 사용자가 대조할 원본이 없어 오류를 인지할 수 없음.
#        원가·수수료·부가세가 순이익 한 값으로 합쳐지므로 개별 항목을 분리 검증한다.
# ===========================================================================

def test_calculation(r: TestRunner):
    page = r.open_app({"ingredients": [재료_배추], "menus": [메뉴_김치찌개],
                       "records": 판매기록_9월4일, "sales": {}})
    s = page.evaluate("() => getDayStats(2026, 9, 4)")

    # 기대값: 7건 × 6,000원
    r.check("TC-A-01", "매출 = 판매수량 × 판매가", abs(s["rev"] - 42000) < 1, s["rev"])

    # 기대값: 7건 × 1,600원
    r.check("TC-A-02", "원가 = 판매수량 × 단위원가", abs(s["cost"] - 11200) < 1, s["cost"])

    # 기대값: 카드 2건 × 6,000원 × 1.5%  (현금 5건은 수수료 없음)
    r.check("TC-A-03", "카드수수료는 카드 결제분에만 적용", abs(s["fee"] - 180) < 1, s["fee"])

    # 부가세는 매출의 1/11
    r.check("TC-A-04", "부가세 = 매출 / 11", abs(s["vat"] - 42000 / 11) < 1, s["vat"])

    # 순이익이 네 항목의 조합과 일치하는지 (중복 차감·누락 검출)
    expected = s["rev"] - s["cost"] - s["fee"] - s["vat"]
    r.check("TC-A-05", "순이익 = 매출 − 원가 − 수수료 − 부가세",
            abs(s["profit"] - expected) < 1, s["profit"])
    page.close()


def test_calculation_payment_mix(r: TestRunner):
    """결제수단 구성이 바뀌면 수수료도 따라 바뀌어야 한다 (고정값 하드코딩 검출)."""
    전액현금 = {"2026-9-4": {"1": {"qty": 0, "cardQty": 0, "cashQty": 7, "cardFeeRate": 1.5,
                                "unitCost": 1600, "sellPrice": 6000, "name": "김치찌개"}}}
    page = r.open_app({"ingredients": [재료_배추], "menus": [메뉴_김치찌개],
                       "records": 전액현금, "sales": {}})
    s = page.evaluate("() => getDayStats(2026, 9, 4)")
    r.check("TC-A-06", "전액 현금 결제 시 수수료 0원", abs(s["fee"]) < 1, s["fee"])
    r.check("TC-A-07", "수수료가 0이어도 매출은 동일", abs(s["rev"] - 42000) < 1, s["rev"])
    page.close()


# ===========================================================================
# TC-B. 데이터 저장·정합성  [위험도: 높음]
#   근거: 유실 시 복구 수단이 없음. 또한 이 앱은 합계(records)와 개별기록(sales)을
#        이중으로 보관하므로, 둘이 어긋나면 화면마다 다른 숫자가 표시된다.
# ===========================================================================

def test_sales_recording(r: TestRunner):
    page = r.open_app({"ingredients": [재료_배추], "menus": [메뉴_김치찌개],
                       "records": {}, "sales": {}})
    page.click("text=오늘 판매 기록하기")
    page.wait_for_timeout(400)

    # 현금으로 2건 기록
    for _ in range(2):
        page.evaluate(
            "() => document.querySelector(\"#salesList .sales-item \""
            "+ \"button[onclick*='changeSales'][onclick*=', 1)']\").click()")
        page.wait_for_timeout(200)
    page.click("#dailyModal .done-btn")
    page.wait_for_timeout(400)

    d = r.stored(page)
    key = list(d["records"].keys())[0] if d["records"] else None
    cash = d["records"][key]["1"].get("cashQty", 0) if key else 0

    r.check("TC-B-01", "판매 기록이 저장소에 반영됨", cash == 2, cash)
    # 합계와 개별기록이 어긋나면 통계와 상세내역의 숫자가 달라진다
    r.check("TC-B-02", "합계(records)와 개별기록(sales) 건수 일치",
            len(d["sales"].get(key, [])) == cash if key else False)
    page.close()


def test_card_payment_recording(r: TestRunner):
    page = r.open_app({"ingredients": [재료_배추], "menus": [메뉴_김치찌개],
                       "records": {}, "sales": {}})
    page.click("text=오늘 판매 기록하기")
    page.wait_for_timeout(400)
    page.click("#payCardBtn")  # 결제수단을 카드로 전환
    page.wait_for_timeout(200)
    page.evaluate(
        "() => document.querySelector(\"#salesList .sales-item \""
        "+ \"button[onclick*='changeSales'][onclick*=', 1)']\").click()")
    page.wait_for_timeout(300)
    page.click("#dailyModal .done-btn")
    page.wait_for_timeout(400)

    d = r.stored(page)
    key = list(d["records"].keys())[0] if d["records"] else None
    r.check("TC-B-03", "카드 결제가 카드 수량으로 분리 저장됨",
            bool(key) and d["records"][key]["1"].get("cardQty", 0) == 1)
    page.close()


def test_snapshot_integrity(r: TestRunner):
    """재료값이 바뀌어도 과거 판매기록의 원가는 보존되어야 한다.

    실시간 참조 구조라면 오늘 재료값 인상이 지난달 순이익까지 소급 변경되며,
    사용자는 과거 기록이 바뀐 사실조차 인지할 수 없다.
    """
    page = r.open_app({"ingredients": [재료_배추], "menus": [메뉴_김치찌개],
                       "records": 판매기록_9월4일, "sales": {}})
    before = page.evaluate("() => getDayStats(2026, 9, 4).cost")

    # 재료 단가를 10원 → 100원으로 10배 인상
    page.evaluate("""() => {
        appData.ingredients[0].unitPrice = 100;
        appData.ingredients[0].history.push(
            {date:'2026-09-20', price:100000, weight:1000, unitPrice:100});
        saveData();
    }""")
    page.wait_for_timeout(200)
    after = page.evaluate("() => getDayStats(2026, 9, 4).cost")

    r.check("TC-B-04", "재료값 인상 후에도 과거 원가 불변 (스냅샷 보존)",
            abs(before - after) < 1, f"{before} → {after}")
    page.close()


# ===========================================================================
# TC-C. 재료·메뉴 관리  [위험도: 중간]
#   근거: 잘못 저장되면 이후 모든 원가 계산이 함께 틀어진다.
# ===========================================================================

def test_ingredient_register(r: TestRunner):
    page = r.open_app({"ingredients": [], "menus": [], "records": {}, "sales": {}})
    page.click("text=재료 관리"); page.wait_for_timeout(300)
    page.click("#ingTabNewBtn"); page.wait_for_timeout(300)
    page.fill("#ingName", "배추")
    page.fill("#ingBuyPrice", "10000")
    page.fill("#ingBuyWeight", "1000")
    page.click("#ingTabNewWrap .add-ing-btn")
    page.wait_for_timeout(400)

    d = r.stored(page)
    ing = d["ingredients"][0] if d["ingredients"] else {}
    r.check("TC-C-01", "재료 등록", ing.get("name") == "배추")
    # 10,000원 / 1,000g = 10원/g  — 단가 자동 계산이 이후 원가의 기준이 된다
    r.check("TC-C-02", "g당 단가 자동 계산 = 가격 ÷ 양",
            abs(ing.get("unitPrice", 0) - 10) < 0.01, ing.get("unitPrice"))
    page.close()


def test_ingredient_restock(r: TestRunner):
    page = r.open_app({"ingredients": [재료_배추], "menus": [], "records": {}, "sales": {}})
    page.click("text=재료 관리"); page.wait_for_timeout(300)
    page.click("text=재구매"); page.wait_for_timeout(300)
    page.click("#restockLowStockWrap button"); page.wait_for_timeout(300)
    page.fill(".restock-price", "5000")
    page.fill(".restock-amount", "500")
    page.click("#restockModal .add-ing-btn")
    page.wait_for_timeout(400)

    hist = r.stored(page)["ingredients"][0]["history"]
    r.check("TC-C-03", "재구매 시 구매 이력이 누적됨 (덮어쓰기 아님)", len(hist) == 2, len(hist))
    page.close()


def test_ingredient_edit_delete(r: TestRunner):
    page = r.open_app({"ingredients": [재료_배추], "menus": [], "records": {}, "sales": {}})
    page.click("text=재료 관리"); page.wait_for_timeout(300)
    page.click("#ingEditHeaderBtn"); page.wait_for_timeout(300)
    page.fill("#editName_1", "알배추")
    page.click("#ingredientEditModal .add-ing-btn")
    page.wait_for_timeout(400)
    r.check("TC-C-04", "재료 이름 수정", r.stored(page)["ingredients"][0]["name"] == "알배추")
    page.close()

    # 저장 시 화면이 닫히므로 삭제는 별도 세션에서 검증
    page = r.open_app({"ingredients": [재료_배추], "menus": [], "records": {}, "sales": {}})
    page.click("text=재료 관리"); page.wait_for_timeout(300)
    page.click("#ingEditHeaderBtn"); page.wait_for_timeout(300)
    page.click("#ingEditDelToggleBtn"); page.wait_for_timeout(300)
    page.evaluate("() => document.querySelector('#ingredientEditListWrap .menu-card-x').click()")
    page.wait_for_timeout(400)
    # 완전 삭제가 아닌 soft delete — 과거 판매기록이 참조하는 재료가 사라지면 안 됨
    r.check("TC-C-05", "재료 삭제는 soft delete로 처리",
            r.stored(page)["ingredients"][0]["deleted"] is True)
    page.close()


def test_menu_create_edit_delete(r: TestRunner):
    page = r.open_app({"ingredients": [재료_배추], "menus": [], "records": {}, "sales": {}})
    page.click("text=메뉴 목록"); page.wait_for_timeout(300)
    page.click("#menuListAddBtn"); page.wait_for_timeout(300)
    page.fill("#menuName", "김치찌개")
    page.click("#menuModal .big-view-btn"); page.wait_for_timeout(200)
    page.query_selector(".recipe-ing-select").select_option(index=0)
    page.fill(".recipe-weight", "200")
    page.fill("#menuQty", "10")
    page.fill("#menuPrice", "6000")
    page.click("#menuModal .save-btn")
    page.wait_for_timeout(400)

    m = r.stored(page)["menus"][0]
    r.check("TC-C-06", "메뉴 생성 (이름·판매가·수량·레시피)",
            m["name"] == "김치찌개" and m["sellPrice"] == 6000
            and m["makeQty"] == 10 and len(m["recipe"]) == 1)
    page.close()

    page = r.open_app({"ingredients": [재료_배추], "menus": [메뉴_김치찌개],
                       "records": {}, "sales": {}})
    page.click("text=메뉴 목록"); page.wait_for_timeout(300)
    page.click("#menuListDelToggleBtn"); page.wait_for_timeout(300)
    page.click("#menuListModal >> text=자세히 보기"); page.wait_for_timeout(400)
    page.fill("#menuPrice", "7000")
    page.click("#menuModal .save-btn"); page.wait_for_timeout(400)
    r.check("TC-C-07", "메뉴 판매가 수정", r.stored(page)["menus"][0]["sellPrice"] == 7000)
    page.close()

    page = r.open_app({"ingredients": [재료_배추], "menus": [메뉴_김치찌개],
                       "records": {}, "sales": {}})
    page.click("text=메뉴 목록"); page.wait_for_timeout(300)
    page.click("#menuListDelToggleBtn"); page.wait_for_timeout(300)
    page.evaluate("() => document.querySelector('#menuListModal .menu-card-x').click()")
    page.wait_for_timeout(300)
    # 삭제는 되돌릴 수 없으므로 확인 단계를 반드시 거쳐야 한다
    r.check("TC-C-08", "메뉴 삭제 전 확인 절차 존재",
            page.evaluate("() => document.getElementById('menuDeleteConfirmModal').style.display") == "flex")
    page.evaluate("() => confirmDeleteMenu()")
    page.wait_for_timeout(400)
    r.check("TC-C-09", "확인 후 메뉴 삭제", len(r.stored(page)["menus"]) == 0)
    page.close()


# ===========================================================================
# TC-D. 통계 집계  [위험도: 중간]
#   근거: 사용자가 앱을 쓰는 목적 그 자체. 기간 전환 시 집계 대상이 바뀐다.
# ===========================================================================

def test_statistics(r: TestRunner):
    page = r.open_app({"ingredients": [재료_배추], "menus": [메뉴_김치찌개],
                       "records": 판매기록_9월4일, "sales": {}})
    page.click("#miniCalMonthLabel"); page.wait_for_timeout(300)
    r.check("TC-D-01", "메인 화면 월간 매출 전환", "42,000" in page.inner_text("#heroRev"))
    page.close()

    page = r.open_app({"ingredients": [재료_배추], "menus": [메뉴_김치찌개],
                       "records": 판매기록_9월4일, "sales": {}})
    page.click("text=자세히 보기"); page.wait_for_timeout(300)
    page.evaluate("() => selectCalendarDay(4)"); page.wait_for_timeout(300)
    r.check("TC-D-02", "달력 일별 내역 조회", "42,000" in page.inner_text("#dayDetailWrap"))

    page.click("text=매출 확인"); page.wait_for_timeout(400)
    r.check("TC-D-03", "월간 통계 - 매출", "42,000" in page.inner_text("#monthlyRevBig"))
    r.check("TC-D-04", "월간 통계 - 메뉴별 순위", "김치찌개" in page.inner_text("#monthlyRankWrap"))
    r.check("TC-D-05", "월간 통계 - 결제수단 비율", "%" in page.inner_text("#monthlyCardPct"))

    for tab, cid in [("#tabYearBtn", "TC-D-06"), ("#tabAllBtn", "TC-D-07")]:
        page.click(tab); page.wait_for_timeout(300)
        r.check(cid, f"기간 탭 전환 ({tab})", "42,000" in page.inner_text("#monthlyRevBig"))
    page.close()


def test_top10_and_more(r: TestRunner):
    """메뉴가 10개를 넘을 때만 드러나는 경계 조건."""
    menus = [{"id": i, "name": f"메뉴{i}", "sellPrice": 5000, "makeQty": 20,
              "recipe": [], "needsSetup": False, "editHistory": []} for i in range(1, 13)]
    recs = {f"2026-9-{(i % 28) + 1}": {str(i): {
        "qty": 0, "cardQty": i, "cashQty": i, "cardFeeRate": 1.5,
        "unitCost": 1000, "sellPrice": 5000, "name": f"메뉴{i}"}} for i in range(1, 13)}

    page = r.open_app({"ingredients": [], "menus": menus, "records": recs, "sales": {}})
    page.click("text=자세히 보기"); page.wait_for_timeout(300)
    page.click("text=매출 확인"); page.wait_for_timeout(400)

    r.check("TC-D-08", "메뉴 12개일 때 상위 10개만 노출",
            len(page.query_selector_all("#monthlyRankWrap .rank-row")) == 10)
    r.check("TC-D-09", "10개 초과 시 더보기 버튼 노출",
            page.evaluate("() => getComputedStyle("
                          "document.getElementById('monthlyRankMoreBtn')).display") == "block")
    page.click("#monthlyRankMoreBtn"); page.wait_for_timeout(400)
    r.check("TC-D-10", "더보기에서 전체 12개 노출",
            len(page.query_selector_all("#rankFullWrap .rank-row")) == 12)
    page.close()


def test_records_view_merge(r: TestRunner):
    """현금·카드로 나뉜 기록이 메뉴 단위로 합산 표시되는지."""
    sales = {"2026-9-4": [
        {"id": 1, "menuId": 1, "name": "김치찌개", "pay": "cash", "unitCost": 1600, "sellPrice": 6000, "time": "10:00"},
        {"id": 2, "menuId": 1, "name": "김치찌개", "pay": "cash", "unitCost": 1600, "sellPrice": 6000, "time": "10:01"},
        {"id": 3, "menuId": 1, "name": "김치찌개", "pay": "card", "unitCost": 1600, "sellPrice": 6000, "time": "10:02"}]}
    recs = {"2026-9-4": {"1": {"qty": 0, "cardQty": 1, "cashQty": 2, "cardFeeRate": 1.5,
                               "unitCost": 1600, "sellPrice": 6000, "name": "김치찌개"}}}
    page = r.open_app({"ingredients": [], "menus": [메뉴_김치찌개], "records": recs, "sales": sales})
    page.click("text=자세히 보기"); page.wait_for_timeout(300)
    page.evaluate("() => selectCalendarDay(4)"); page.wait_for_timeout(300)
    page.click("text=기록 보기"); page.wait_for_timeout(400)

    r.check("TC-D-11", "결제수단 무관하게 메뉴 단위 합산 (2+1=3)",
            "3개" in page.inner_text("#salesViewWrap"))
    r.check("TC-D-12", "카드 비율 = 1/3 ≈ 33%",
            "33" in page.inner_text("#salesViewCardPct"),
            page.inner_text("#salesViewCardPct"))
    page.close()


# ===========================================================================
# TC-E. 화면 이동  [위험도: 중간]
#   근거: 뒤로가기가 의도와 다르게 동작하면 사용자는 길을 잃고 이탈한다.
#        동일 원인 결함이 4개 화면에서 반복 발견되어 전수 점검 대상으로 승격.
# ===========================================================================

def test_navigation(r: TestRunner):
    비용토글 = {"ingredients": [재료_배추], "menus": [메뉴_김치찌개],
              "records": {}, "sales": {}, "costToggles": 비용항목_전체}

    # 비용관리 → 재료 로스율 → 뒤로가기 → 비용관리로 복귀해야 함
    page = r.open_app(비용토글)
    page.click("text=비용 관리"); page.wait_for_timeout(300)
    page.click("#costManageListWrap >> text=재료 로스율"); page.wait_for_timeout(400)
    r.check("TC-E-01", "비용관리 → 로스율 화면 진입", page.is_visible("#ingredientEditModal"))
    page.click("#ingredientEditModal .back-btn"); page.wait_for_timeout(400)
    r.check("TC-E-02", "뒤로가기 시 비용관리로 복귀 (건너뛰기 없음)",
            page.evaluate("() => navStack") == ["costManageModal"],
            page.evaluate("() => navStack"))
    page.close()

    # 메뉴목록 → 메뉴수정 → 뒤로가기 → 메뉴목록 유지
    page = r.open_app({"ingredients": [재료_배추], "menus": [메뉴_김치찌개],
                       "records": {}, "sales": {}})
    page.click("text=메뉴 목록"); page.wait_for_timeout(300)
    page.click("#menuListDelToggleBtn"); page.wait_for_timeout(300)
    page.click("#menuListModal >> text=자세히 보기"); page.wait_for_timeout(400)
    page.click("#menuModal .back-btn"); page.wait_for_timeout(400)
    r.check("TC-E-03", "메뉴수정 뒤로가기 시 메뉴목록 유지",
            page.evaluate("() => navStack") == ["menuListModal"],
            page.evaluate("() => navStack"))
    page.close()

    # 수정 모드에서 뒤로가기 → 화면을 닫지 말고 모드만 해제해야 함
    page = r.open_app({"ingredients": [], "menus": [], "records": {}, "sales": {}})
    page.click("text=메뉴 목록"); page.wait_for_timeout(300)
    page.click("#menuListDelToggleBtn"); page.wait_for_timeout(300)
    page.click("#menuListModal .back-btn"); page.wait_for_timeout(300)
    r.check("TC-E-04", "수정 모드 뒤로가기 시 모드만 해제, 화면 유지",
            page.evaluate("() => menuListDeleteMode") is False
            and page.evaluate("() => navStack") == ["menuListModal"])
    page.close()


# ===========================================================================
# TC-F. 화면 표시 실측  [위험도: 낮음~중간]
#   근거: 지정값과 실제 적용값이 다른 결함은 스크린샷 검수로 탐지되지 않는다.
#        (실제 사례: 지정 13px → 실제 렌더링 22px)
# ===========================================================================

def test_rendering_values(r: TestRunner):
    page = r.open_app({"ingredients": [], "menus": [메뉴_김치찌개],
                       "records": 판매기록_9월4일, "sales": {}})
    page.click("text=자세히 보기"); page.wait_for_timeout(300)
    page.click("text=매출 확인"); page.wait_for_timeout(400)

    # 전역 글꼴 규칙이 개별 지정을 덮어쓰지 않는지 실측
    사이즈 = page.evaluate("""() => {
        const fs = id => getComputedStyle(document.getElementById(id)).fontSize;
        return {매출: fs('monthlyRevBig'), 원가: fs('monthlyCostBig'),
                정산금: fs('settlementVal'), 카드비율: fs('monthlyCardPct')};
    }""")
    r.check("TC-F-01", "매출 글꼴 = 지정값 32px", 사이즈["매출"] == "32px", 사이즈["매출"])
    r.check("TC-F-02", "원가 글꼴 = 지정값 19px", 사이즈["원가"] == "19px", 사이즈["원가"])
    r.check("TC-F-03", "정산금 글꼴 = 지정값 27px", 사이즈["정산금"] == "27px", 사이즈["정산금"])
    r.check("TC-F-04", "결제비율 글꼴 = 지정값 17px", 사이즈["카드비율"] == "17px", 사이즈["카드비율"])

    # 카드가 컨테이너 폭을 정상적으로 채우는지 (flex 폭 미확장 결함 검출)
    폭 = page.evaluate("""() => {
        const w = el => Math.round(el.getBoundingClientRect().width);
        return [w(document.getElementById('monthlyRevBig').parentElement),
                w(document.getElementById('settlementVal').parentElement)];
    }""")
    r.check("TC-F-05", "카드 폭이 서로 일치 (좌우 여백 동일)", 폭[0] == 폭[1], 폭)
    page.close()


def test_search(r: TestRunner):
    menus = [{"id": i, "name": f"메뉴{i}", "sellPrice": 5000, "makeQty": 20,
              "recipe": [], "needsSetup": False, "editHistory": []} for i in range(1, 13)]
    page = r.open_app({"ingredients": [], "menus": menus, "records": {}, "sales": {}})
    page.click("text=메뉴 목록"); page.wait_for_timeout(300)
    page.fill("#menuSearchInput", "메뉴1"); page.wait_for_timeout(300)
    cnt = len(page.query_selector_all("#menuListWrap .menu-card"))
    r.check("TC-F-06", "메뉴 검색 필터링 동작", 0 < cnt < 12, f"{cnt}건")
    page.close()


# ===========================================================================
# TC-G. 비용 관리 · 데이터 백업  [위험도: 높음 (백업) / 중간 (비용)]
# ===========================================================================

def test_cost_management(r: TestRunner):
    base = {"ingredients": [재료_배추], "menus": [메뉴_김치찌개],
            "records": {}, "sales": {}, "costToggles": 비용항목_전체}

    page = r.open_app(base)
    page.click("text=비용 관리"); page.wait_for_timeout(300)
    page.click("#costManageListWrap >> text=고정비"); page.wait_for_timeout(400)
    inputs = page.query_selector_all("#fixedCostFieldsWrap input")
    if inputs:
        inputs[0].fill("50000")
    page.click("#fixedCostModal .add-ing-btn"); page.wait_for_timeout(400)
    fc = r.stored(page).get("fixedCosts", {})
    r.check("TC-G-01", "고정비 저장", bool(fc) and any(fc.values()), fc)
    page.close()

    page = r.open_app(base)
    page.click("text=비용 관리"); page.wait_for_timeout(300)
    page.click("#costManageListWrap >> text=인건비"); page.wait_for_timeout(400)
    r.check("TC-G-02", "인건비 화면 진입", page.is_visible("#payrollModal"))
    page.click("#payrollManageBtn"); page.wait_for_timeout(300)
    r.check("TC-G-03", "직원 관리 모드 전환 시 제목 변경",
            page.inner_text("#payrollModalTitle") == "직원 관리")
    page.close()

    page = r.open_app(base)
    page.click("text=비용 관리"); page.wait_for_timeout(300)
    page.click("#costManageListWrap >> text=감가상각비"); page.wait_for_timeout(400)
    r.check("TC-G-04", "감가상각비 화면 진입", page.is_visible("#depreciationModal"))
    page.close()


def test_backup_export(r: TestRunner):
    """localStorage 저장 구조라 백업이 유일한 복구 수단 — 반드시 동작해야 한다."""
    page = r.open_app({"ingredients": [재료_배추], "menus": [메뉴_김치찌개],
                       "records": 판매기록_9월4일, "sales": {}})
    page.click("button[aria-label='설정']"); page.wait_for_timeout(300)
    page.click("#settingsModal >> text=더보기"); page.wait_for_timeout(300)
    with page.expect_download(timeout=15000) as dl:
        page.click("#moreSheetModal >> text=데이터 저장하기")
    r.check("TC-G-05", "데이터 백업 파일 다운로드",
            dl.value.suggested_filename.endswith(".zip"), dl.value.suggested_filename)
    page.close()


def test_settings(r: TestRunner):
    page = r.open_app({"ingredients": [], "menus": [], "records": {}, "sales": {}})
    page.click("button[aria-label='설정']"); page.wait_for_timeout(300)
    page.click("text=UI 크기"); page.wait_for_timeout(300)
    page.click("#sizeOptLarge"); page.wait_for_timeout(200)
    page.click("#screenSizeModal .add-menu-btn"); page.wait_for_timeout(400)
    r.check("TC-G-06", "UI 크기 설정 저장",
            page.evaluate("() => localStorage.getItem('seniorLedgerScreenSize')") == "large")
    page.close()

    page = r.open_app({"ingredients": [], "menus": [], "records": {}, "sales": {}})
    page.click("button[aria-label='설정']"); page.wait_for_timeout(300)
    page.click("#settingsModal >> text=비용 항목"); page.wait_for_timeout(300)
    page.evaluate("() => document.querySelector('#costToggleListWrap .cost-switch').click()")
    page.wait_for_timeout(200)
    page.click("#costToggleModal .add-menu-btn"); page.wait_for_timeout(400)
    r.check("TC-G-07", "비용 항목 설정 저장",
            any(r.stored(page).get("costToggles", {}).values()))
    page.close()


# ===========================================================================

SUITES = [
    ("TC-A 계산 정확도", [test_calculation, test_calculation_payment_mix]),
    ("TC-B 데이터 저장·정합성", [test_sales_recording, test_card_payment_recording,
                          test_snapshot_integrity]),
    ("TC-C 재료·메뉴 관리", [test_ingredient_register, test_ingredient_restock,
                       test_ingredient_edit_delete, test_menu_create_edit_delete]),
    ("TC-D 통계 집계", [test_statistics, test_top10_and_more, test_records_view_merge]),
    ("TC-E 화면 이동", [test_navigation]),
    ("TC-F 화면 표시 실측", [test_rendering_values, test_search]),
    ("TC-G 비용 관리·백업", [test_cost_management, test_backup_export, test_settings]),
]


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        runner = TestRunner(browser)
        for title, tests in SUITES:
            print(f"\n--- {title} ---")
            for t in tests:
                t(runner)
        ok = runner.summary()
        browser.close()
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
