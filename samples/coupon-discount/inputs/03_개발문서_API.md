# [개발] 쿠폰 적용 API 설계 v0.3
작성: 주문개발팀 / 2026-11-03

## POST /orders/{orderId}/coupon
| 필드 | 타입 | 설명 |
|---|---|---|
| couponId | string | 적용할 쿠폰 |

### 검증 로직
- `totalPrice >= 30000` (totalPrice = 상품 금액 + 배송비)
- 주문에 이미 쿠폰이 적용돼 있으면 `409 COUPON_ALREADY_APPLIED`
- 카테고리가 `excludedCategories`(GIFT_CARD, E_COUPON)에 해당하는 상품은 할인 계산에서 제외

### 할인 계산
- 정률: `min(대상금액 × rate, maxDiscount)` — maxDiscount는 **쿠폰별 설정값**
- 원 단위 절사
- 할인 후 결제 금액은 **최소 100원** (0원 결제 불가)

### 쿠폰 상태 변경
- 쿠폰 상태 `USED` 변경은 **결제 승인 완료 후** 처리
- 취소: **전체 취소 시에만** `AVAILABLE`로 복구, 부분 취소는 복구하지 않음
