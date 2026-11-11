# [QA 서버] 주문·쿠폰 API 명세 (샘플)
가상 "쿠폰 할인" 기능의 QA 서버 API. 실제 회사의 Swagger·API 문서에 해당한다.
동작 규칙은 `inputs/03_개발문서_API.md`(v0.3)를 따른다. 금액 단위는 원, 시각은 서버 시각(KST, `YYYY-MM-DDTHH:MM:SS`).

- 기본 주소: `http://127.0.0.1:8765`
- 인증: 헤더 `X-QA-Token: <토큰>` (샘플 서버 토큰 `sample-token`)
- 요청·응답 본문: JSON. 오류는 `{"code": "...", "message": "..."}`

## 테스트 데이터 준비 (QA 서버 전용 `/test/*`)
| 메서드·경로 | 본문 | 응답 | 설명 |
|---|---|---|---|
| POST `/test/reset` | — | `{}` | 모든 데이터·시각·PG 설정 초기화 |
| POST `/test/members` | `{"points": 5000}` | `{"memberId"}` | 테스트 회원 생성(포인트 보유량) |
| POST `/test/coupons` | `{"memberId", "type": "FIXED"\|"RATE", "amount"?, "rate"?, "maxDiscount"?, "expiresAt"?}` | `{"couponId"}` | 쿠폰 발급. FIXED는 `amount`, RATE는 `rate`(10 = 10%)·`maxDiscount`. `expiresAt` 없으면 30일 뒤 |
| POST `/test/clock` | `{"now": "2026-11-10T23:59:58"}` | `{"now"}` | 서버 시각 고정. `{"now": null}`이면 실제 시각 |
| POST `/test/pg` | `{"mode": "approve"\|"decline"}` | `{"mode"}` | 다음 결제부터 PG 승인 결과 |

## 주문
### POST `/orders` — 주문서 만들기
요청 `{"memberId", "items": [{"name", "price", "category"}], "shippingFee"}`
- `category`: `GENERAL`, `GIFT_CARD`(상품권), `E_COUPON`(e쿠폰)
- 응답: 주문서(아래 GET과 같음), `201`

### GET `/orders/{orderId}` — 주문서·주문 조회
```json
{
  "orderId": "O-1", "memberId": "M-1", "status": "CREATED",
  "items": [{"index": 0, "name": "일반", "price": 40000, "category": "GENERAL", "cancelled": false}],
  "goodsAmount": 40000, "excludedAmount": 0, "shippingFee": 3000, "totalPrice": 43000,
  "appliedCouponId": null, "couponDiscount": 0, "usedPoints": 0, "payAmount": 43000,
  "autoSelectedCouponId": "C-2",
  "coupons": [{"couponId": "C-2", "type": "FIXED", "discount": 5000, "usable": true, "reason": null,
               "expiresAt": "2026-12-10T23:59:59", "daysLeft": 30, "soonExpiring": false}],
  "refundedAmount": 0, "pgApprovedAmount": null
}
```
- `goodsAmount`: 상품 금액 합계(제외 카테고리 포함), `excludedAmount`: 그중 제외 카테고리 금액
- `totalPrice` = `goodsAmount` + `shippingFee`
- `coupons`: 회원의 쿠폰 목록. 이 주문에 썼을 때의 `discount` 큰 순 정렬. `usable=false`면 `reason`(`MIN_ORDER_AMOUNT_NOT_MET`, `EXPIRED`, `USED`)
- `autoSelectedCouponId`: 사용 가능한 쿠폰 중 할인액이 가장 큰 쿠폰(주문서 자동 선택)
- `soonExpiring`: 만료까지 1~3일(`daysLeft` = 만료일 − 오늘, 날짜 기준)
- `payAmount` = `totalPrice` − `couponDiscount` − `usedPoints`

### POST `/orders/{orderId}/coupon` — 쿠폰 적용
요청 `{"couponId"}` → `200` 주문서
| 상태 | code | 언제 |
|---|---|---|
| 400 | `MIN_ORDER_AMOUNT_NOT_MET` | `totalPrice < 30000` |
| 400 | `COUPON_EXPIRED` | 만료된 쿠폰 |
| 400 | `COUPON_NOT_AVAILABLE` | 이미 USED인 쿠폰 |
| 409 | `COUPON_ALREADY_APPLIED` | 이 주문에 이미 쿠폰이 적용됨 |

### DELETE `/orders/{orderId}/coupon` — 쿠폰 해제
→ `200` 주문서 (쿠폰을 바꿀 때는 해제 후 다시 적용)

### POST `/orders/{orderId}/points` — 포인트 사용
요청 `{"amount"}` → `200` 주문서. 쿠폰 할인 뒤 금액에서 차감하며, 결제 금액은 100원 아래로 내려가지 않는다(넘는 만큼은 사용되지 않음). 보유량보다 많으면 `400 INSUFFICIENT_POINTS`.

### POST `/orders/{orderId}/pay` — 결제
→ `200` `{"orderId", "status": "PAID", "approvedAmount"}`
- PG 승인 성공 시 쿠폰 `USED`, 포인트 차감
| 상태 | code | 언제 |
|---|---|---|
| 402 | `PG_DECLINED` | PG 승인 실패(쿠폰·포인트 변화 없음) |
| 409 | `COUPON_ALREADY_USED` | 적용한 쿠폰이 다른 주문에서 먼저 사용됨 |

### POST `/orders/{orderId}/cancel` — 취소
요청 `{"itemIndexes": [1]}` (부분 취소) 또는 `{}` (남은 상품 전체 취소) → `200` `{"orderId", "status", "refundAmount"}`
- 부분 취소 환불: 상품 금액 − 쿠폰 할인 안분(상품 금액 비율, 원 단위 절사). 마지막 취소는 남은 결제 금액 전부
- 쿠폰: 전체 취소(`{}`)일 때만 `AVAILABLE`로 복구

## 조회
| 메서드·경로 | 응답 |
|---|---|
| GET `/coupons/{couponId}` | `{"couponId", "status": "AVAILABLE"\|"USED", "usedOrderId", "expiresAt", ...}` |
| GET `/members/{memberId}` | `{"memberId", "points"}` |
