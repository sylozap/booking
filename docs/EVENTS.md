# Реестр событий

## 1. Конверт

Каждое событие публикуется в едином конверте:

```
{
  "event_id":     "uuid",            // идемпотентность у потребителя
  "event_type":   "BookingConfirmed",
  "version":      1,                 // версия схемы payload
  "occurred_at":  "RFC3339 UTC",
  "tenant_id":    "uuid",            // организация
  "partition_key":"specialist_id",   // порядок событий в рамках специалиста
  "trace_id":     "...",             // сквозная трассировка из OpenTelemetry
  "payload":      { ... }
}
```

`partition_key = specialist_id` для всех событий бронирования: это гарантирует
строгий порядок событий по одному специалисту, а параллелизм сохраняется между
специалистами.

## 2. Каталог

| Событие | Владелец | Потребители | Payload (существенное) |
|---|---|---|---|
| `UserRegistered` | identity | notification, catalog | user_id, email, full_name, timezone |
| `UserProfileUpdated` | identity | notification | user_id, изменённые поля |
| `SpecialistInvited` | catalog | identity, notification | organization_id, email, specialist_id, invited_by |
| `OrganizationCreated` | catalog | identity, notification | organization_id, owner_user_id |
| `SpecialistRoleGranted` | identity | catalog, notification | user_id, organization_id, role |
| `OfferingUpdated` | catalog | booking | specialist_id, service_id, organization_id, duration_min, buffer_min, price_amount, currency, is_active, timezone |
| `OfferingDeactivated` | catalog | booking | specialist_id, service_id |
| `SpecialistDeactivated` | catalog | booking | specialist_id |
| `BookingHeld` | booking | payment, notification | booking_id, client_user_id, specialist_id, amount, currency, hold_expires_at |
| `BookingConfirmed` | booking | payment, notification | booking_id, интервал, участники |
| `BookingExpired` | booking | payment, notification | booking_id, причина |
| `BookingCancelled` | booking | payment, notification | booking_id, cancelled_by, refund_required |
| `BookingRescheduled` | booking | notification | booking_id, старый и новый интервал |
| `BookingCompleted` | booking | notification | booking_id |
| `BookingNoShow` | booking | notification | booking_id |
| `PaymentVoidRequested` | booking | payment | booking_id, payment_id |
| `PaymentAuthorized` | payment | booking | booking_id, payment_id, amount |
| `PaymentFailed` | payment | booking, notification | booking_id, reason |
| `PaymentCaptured` | payment | booking | booking_id, payment_id |
| `PaymentRefunded` | payment | booking, notification | booking_id, amount |

### Компактный топик

`OfferingUpdated` публикуется в **compacted** топик с ключом
`(specialist_id, service_id)`: перестроение проекции `offerings` не зависит от
политики retention (ADR-0012). Следствие — история изменений цены по этому топику
недоступна, хранится только последнее состояние.

## 3. Правила контрактов

1. **Событие — факт прошлого, не команда.** Имя в прошедшем времени. Исключение —
   `PaymentVoidRequested`: это команда оркестратора исполнителю в рамках саги,
   и она названа так намеренно.
2. **Публикуется доменный смысл, а не строки таблиц.** `OfferingUpdated` несёт уже
   разрешённые эффективные значения (с учётом переопределений специалиста).
   `booking` не знает о существовании таблицы `specialist_services`.
3. **Только через outbox**, в одной транзакции с изменением состояния.
4. **Потребитель идемпотентен** — дедупликация по `event_id` в `processed_events`.
5. Потребитель **игнорирует неизвестные поля** (tolerant reader).

## 4. Версионирование

Обратно совместимые изменения (добавление опционального поля) — без смены `version`.

Несовместимые изменения (удаление или переименование поля, смена семантики):

1. публикуется новая версия события **параллельно** со старой;
2. потребители переводятся на новую;
3. старая версия снимается с публикации.

Одновременная публикация двух версий обязательна: у одного разработчика нет
возможности остановить систему для синхронного обновления всех потребителей,
и приучаться к этому не следует.

## 5. Обработка отказов у потребителя

| Тип ошибки | Поведение |
|---|---|
| Транзиентная (БД недоступна, таймаут) | ретрай с экспоненциальной задержкой |
| Постоянная (невалидный payload, битая логика) | после N попыток → dead letter topic |
| Дубликат | тихо игнорируется по `processed_events` |

Dead letter topic обязателен: молча теряемое событие в системе с деньгами
недопустимо.
