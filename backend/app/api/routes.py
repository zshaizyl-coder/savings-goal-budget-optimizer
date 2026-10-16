from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from backend.app.api.deps import current_user, get_session
from backend.app.api.errors import BusinessError
from backend.app.api.schemas import (
    BudgetGenerateRequest, CategoryUpdate, ConstraintSet, EventCreate, GoalCreate,
    LoginRequest, ModelCategoryBatchRequest, RegisterRequest, ReplanRequestBody,
    TransactionPlanningUpdate,
)
from backend.app.classification.deepseek_provider import (
    DEEPSEEK_PROMPT_VERSION,
    DeepSeekClassificationProvider,
)
from backend.app.classification.features import is_cloud_eligible, privacy_minimized_features
from backend.app.classification.openai_provider import ClassificationProviderError
from backend.app.classification.merchant_search import (
    VolcengineMerchantSearchProvider,
    MerchantSearchError,
)
from backend.app.classification.types import ClassificationContext
from backend.app.domain.contracts import FinancialEventSpec, FinancialSnapshotSpec
from backend.app.domain.enums import (
    AdjustmentMode, BudgetStrategy, FinancialEventType, ImpactScope,
    ReplanTriggerSource,
)
from backend.app.models import (
    BudgetConstraint, BudgetPlan, BudgetPlanEvent, BudgetPlanItemModel, Category,
    ClassificationSuggestion, FinancialEvent, FinancialSnapshot, ImportBatch,
    IncomeAssumption, SavingGoal, Transaction, User,
)
from backend.app.optimization.interfaces import ReplanRequest
from backend.app.replanning import (
    PersistReplanCommand, ReplanPersistenceService, RollingBudgetReplanner,
    calculate_weekly_variance, choose_replan_trigger,
)
from backend.app.repositories.categories import CategoryRepository
from backend.app.security import create_access_token, hash_password, verify_password
from backend.app.services.hybrid_classification import HybridClassificationService
from backend.app.services.category_review_queue import PrivateCategoryReviewQueue
from backend.app.services.import_wechat import WechatImportService
from backend.app.services.web_finance import (
    ADJUSTABILITY_BPS, WebFinanceService, dataframe_records, planning_weeks,
    serialize_optimization,
)


router = APIRouter(prefix="/api/v1")


def _owned(session: Session, model, object_id: int, user_id: int, code: str):
    item = session.scalar(select(model).where(model.id == object_id, model.user_id == user_id))
    if item is None:
        raise BusinessError(code, "资源不存在或不属于当前用户。", 404)
    return item


@router.post("/auth/register")
def register(body: RegisterRequest, request: Request, session: Session = Depends(get_session)):
    if session.scalar(select(User.id).where(User.username == body.username)) is not None:
        raise BusinessError("USERNAME_EXISTS", "该用户名已存在。", 409)
    try:
        password_hash = hash_password(body.password)
    except ValueError as exc:
        raise BusinessError("WEAK_PASSWORD", "密码至少需要8个字符。", 422) from exc
    user = User(username=body.username, password_hash=password_hash, timezone=body.timezone)
    session.add(user); session.commit()
    return {"access_token": create_access_token(user.id, request.app.state.settings.secret_key),
            "token_type": "bearer", "user": {"id": user.id, "username": user.username}}


@router.post("/auth/login")
def login(body: LoginRequest, request: Request, session: Session = Depends(get_session)):
    user = session.scalar(select(User).where(User.username == body.username))
    if user is None or not verify_password(body.password, user.password_hash):
        raise BusinessError("INVALID_CREDENTIALS", "用户名或密码不正确。", 401)
    return {"access_token": create_access_token(user.id, request.app.state.settings.secret_key),
            "token_type": "bearer", "user": {"id": user.id, "username": user.username}}


@router.get("/users/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "username": user.username, "timezone": user.timezone}


@router.post("/imports/wechat")
async def import_wechat(
    request: Request, file: UploadFile = File(...), user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix != ".xlsx":
        raise BusinessError("UNSUPPORTED_SOURCE_FORMAT", "微信账单仅支持.xlsx文件。", 415)
    content = await file.read(request.app.state.settings.upload_max_bytes + 1)
    if len(content) > request.app.state.settings.upload_max_bytes:
        raise BusinessError("FILE_TOO_LARGE", "文件超过上传大小限制。", 413)
    if not content.startswith(b"PK"):
        raise BusinessError("INVALID_WORKBOOK", "文件不是有效的Excel工作簿。", 422)
    private_dir = Path("data/private"); private_dir.mkdir(parents=True, exist_ok=True)
    path = None
    try:
        with tempfile.NamedTemporaryFile(dir=private_dir, suffix=".xlsx", delete=False) as handle:
            handle.write(content); path = Path(handle.name)
        secret = request.app.state.settings.secret_key.encode()
        report = WechatImportService(session, secret).import_file(path, username=user.username)
        review_groups = 0
        if report.status != "failed":
            review_groups = PrivateCategoryReviewQueue().rebuild(
                session, path, user_id=user.id, fingerprint_secret=secret, batch_id=report.batch_id,
            )
        session.commit()
        return report.to_dict() | {"review_group_count": review_groups}
    finally:
        if path is not None:
            path.unlink(missing_ok=True)


@router.get("/imports")
def imports(user: User = Depends(current_user), session: Session = Depends(get_session)):
    rows = session.scalars(select(ImportBatch).where(
        ImportBatch.user_id == user.id
    ).order_by(ImportBatch.started_at.desc())).all()
    return {"items": [{
        "id": row.id, "source": row.source, "status": row.status,
        "started_at": row.started_at.isoformat(), "total_rows": row.total_rows,
        "inserted_rows": row.inserted_rows, "duplicate_rows": row.duplicate_rows,
        "failed_rows": row.failed_rows, "warning_rows": row.warning_rows,
    } for row in rows]}


@router.get("/categories")
def categories(user: User = Depends(current_user), session: Session = Depends(get_session)):
    del user
    rows = CategoryRepository(session).ensure_defaults(); session.commit()
    return {"items": [{"code": code, "name": item.display_name}
                      for code, item in sorted(rows.items(), key=lambda pair: pair[1].sort_order)]}


@router.get("/transactions")
def transactions(
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
    category_code: str | None = Query(default=None, max_length=40),
    user: User = Depends(current_user), session: Session = Depends(get_session),
):
    suggested = aliased(Category); confirmed = aliased(Category)
    base = (select(Transaction, suggested.code, confirmed.code)
            .outerjoin(suggested, Transaction.suggested_category_id == suggested.id)
            .outerjoin(confirmed, Transaction.user_category_id == confirmed.id)
            .where(Transaction.user_id == user.id))
    if category_code:
        valid_codes = CategoryRepository(session).ensure_defaults()
        if category_code not in valid_codes:
            raise BusinessError("INVALID_CATEGORY", "分类不存在。", 422)
        base = base.where(func.coalesce(confirmed.code, suggested.code, "other") == category_code)
    count_query = select(func.count()).select_from(base.subquery())
    total = session.scalar(count_query) or 0
    rows = session.execute(base.order_by(Transaction.occurred_at_utc.desc())
                           .offset((page - 1) * page_size).limit(page_size)).all()
    private_details = PrivateCategoryReviewQueue().load(user.id).get("transaction_details", {})
    return {"items": [{
        "id": item.id, "occurred_at": item.occurred_at_utc.isoformat(),
        "amount_minor": item.amount_minor, "direction": item.direction,
        "flow_type": item.flow_type, "source": item.source,
        "source_transaction_type": item.source_type_code,
        "status": item.normalized_status, "original_category": item.original_category,
        "system_category": system_code or "other",
        "user_category": user_code,
        "effective_category": user_code or system_code or "other",
        "user_confirmed": user_code is not None,
        "classification_source": "user" if user_code else "system" if system_code else "unclassified",
        "planning_role": item.planning_role,
        "recurrence_frequency": item.recurrence_frequency,
        "next_expected_date": item.next_expected_date.isoformat() if item.next_expected_date else None,
        "expected_amount_minor": item.expected_amount_minor,
        "counterparty_hint": private_details.get(str(item.id), {}).get("counterparty_hint", ""),
        "description_hint": private_details.get(str(item.id), {}).get("description_hint", ""),
    } for item, system_code, user_code in rows], "total": total, "page": page, "page_size": page_size}


@router.patch("/transactions/{transaction_id}/planning")
def update_transaction_planning(
    transaction_id: int, body: TransactionPlanningUpdate,
    user: User = Depends(current_user), session: Session = Depends(get_session),
):
    item = _owned(session, Transaction, transaction_id, user.id, "TRANSACTION_NOT_FOUND")
    if body.planning_role != "routine" and not (
        item.flow_type == "consumption" and item.direction == "outflow"
    ):
        raise BusinessError(
            "INVALID_PLANNING_ROLE",
            "只有实际消费支出可以标记为低频或一次性支出。",
            422,
        )
    item.planning_role = body.planning_role
    if body.planning_role == "routine":
        item.recurrence_frequency = None
        item.next_expected_date = None
        item.expected_amount_minor = None
    else:
        item.recurrence_frequency = body.recurrence_frequency
        item.next_expected_date = body.next_expected_date
        item.expected_amount_minor = body.expected_amount_minor
    # A reliably linked refund follows the original expense's historical role,
    # but never creates a second future commitment of its own.
    linked_refunds = session.scalars(select(Transaction).where(
        Transaction.user_id == user.id,
        Transaction.related_transaction_id == item.id,
        Transaction.flow_type == "expense_refund",
    )).all()
    for refund in linked_refunds:
        refund.planning_role = body.planning_role
        refund.recurrence_frequency = None
        refund.next_expected_date = None
        refund.expected_amount_minor = None
    session.commit()
    return {"id": item.id, "planning_role": item.planning_role,
            "recurrence_frequency": item.recurrence_frequency,
            "next_expected_date": item.next_expected_date.isoformat() if item.next_expected_date else None,
            "expected_amount_minor": item.expected_amount_minor}


@router.patch("/transactions/{transaction_id}/category")
def update_category(
    transaction_id: int, body: CategoryUpdate, user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    item = _owned(session, Transaction, transaction_id, user.id, "TRANSACTION_NOT_FOUND")
    scope = "counterparty" if item.counterparty_fingerprint else "transaction"
    try:
        changed = HybridClassificationService(session).confirm_manual(
            user_id=user.id, transaction_id=item.id,
            category_code=body.category_code, scope=scope,
        )
    except ValueError as exc:
        raise BusinessError("INVALID_CATEGORY", "分类不存在。", 422) from exc
    session.commit()
    return {"updated_transactions": changed, "category_code": body.category_code,
            "user_confirmed": True}


@router.get("/categories/review")
def category_review(user: User = Depends(current_user), session: Session = Depends(get_session)):
    other_id = session.scalar(select(Category.id).where(Category.code == "other"))
    total_count, total_amount = session.execute(select(
        func.count(Transaction.id), func.coalesce(func.sum(Transaction.amount_minor), 0),
    ).where(Transaction.user_id == user.id, Transaction.flow_type == "consumption")).one()
    other_count, other_amount = session.execute(select(
        func.count(Transaction.id), func.coalesce(func.sum(Transaction.amount_minor), 0),
    ).where(
        Transaction.user_id == user.id, Transaction.flow_type == "consumption",
        func.coalesce(Transaction.user_category_id, Transaction.suggested_category_id) == other_id,
    )).one()
    summary = {
        "consumption_count": int(total_count),
        "consumption_amount_minor": int(total_amount),
        "current_other_count": int(other_count), "current_other_amount_minor": int(other_amount),
        "current_other_count_ratio": (int(other_count) / int(total_count) if total_count else 0),
        "current_other_amount_ratio": (int(other_amount) / int(total_amount) if total_amount else 0),
    }
    private = PrivateCategoryReviewQueue().load(user.id)
    if private["items"]:
        transaction_ids = [item["representative_transaction_id"] for item in private["items"]]
        still_other = set(session.scalars(select(Transaction.id).where(
            Transaction.id.in_(transaction_ids), Transaction.user_id == user.id,
            Transaction.flow_type == "consumption", Transaction.user_category_id.is_(None),
            Transaction.suggested_category_id == other_id,
        )).all())
        items = [dict(item) for item in private["items"]
                 if item["representative_transaction_id"] in still_other]
        suggestion_rows = session.execute(select(
            ClassificationSuggestion, Category.code,
        ).join(Category, ClassificationSuggestion.suggested_category_id == Category.id).where(
            ClassificationSuggestion.user_id == user.id,
            ClassificationSuggestion.transaction_id.in_(transaction_ids),
            ClassificationSuggestion.provider == "deepseek",
            ClassificationSuggestion.status == "pending",
        ).order_by(ClassificationSuggestion.created_at.desc())).all()
        suggestions = {}
        for suggestion, category_code in suggestion_rows:
            suggestions.setdefault(suggestion.transaction_id, {
                "id": suggestion.id, "category_code": category_code,
                "confidence": suggestion.confidence_bps / 100,
                "evidence_codes": json.loads(suggestion.evidence_codes),
                "model_name": suggestion.model_name,
            })
        for item in items:
            item["model_suggestion"] = suggestions.get(item["representative_transaction_id"])
        return {"items": items, "generated_at": private.get("generated_at"), "summary": summary,
                "meaning": "ABC仅表示财务影响和审核优先级，不表示消费必要性。商户短文本仅来自本机私有审核队列。"}
    rows = session.execute(select(
        Transaction.counterparty_fingerprint, func.sum(Transaction.amount_minor), func.count(Transaction.id)
    ).where(
        Transaction.user_id == user.id, Transaction.flow_type == "consumption",
        Transaction.user_category_id.is_(None), Transaction.suggested_category_id == other_id,
    ).group_by(Transaction.counterparty_fingerprint).order_by(func.sum(Transaction.amount_minor).desc())).all()
    total = sum(int(amount) for _fp, amount, _count in rows) or 1; cumulative = 0
    items = []
    for index, (fingerprint, amount, count) in enumerate(rows):
        cumulative += int(amount); share = cumulative / total
        tier = "A" if share <= .7 or index == 0 else "B" if share <= .9 or count >= 3 else "C"
        items.append({"group_id": (fingerprint or f"single-{index}")[:12], "tier": tier,
                      "amount_minor": int(amount), "transaction_count": int(count)})
    return {"items": items, "summary": summary,
            "meaning": "ABC仅表示财务影响和审核优先级，不表示消费必要性。"}


@router.get("/classification/providers/deepseek")
def deepseek_status(request: Request, user: User = Depends(current_user)):
    del user
    settings = request.app.state.settings
    return {
        "enabled": settings.deepseek_enabled,
        "configured": bool(settings.deepseek_api_key),
        "ready": settings.deepseek_enabled and bool(settings.deepseek_api_key),
        "model": settings.deepseek_model,
        "web_search_ready": (
            settings.merchant_search_enabled and bool(settings.merchant_search_api_key)
        ),
        "web_search_provider": "volcengine" if settings.merchant_search_enabled else None,
        "privacy": (
            "DeepSeek仅接收脱敏短文本、金额区间、时段和频次区间；"
            "公开检索仅发送商户短名称，个人二维码默认不发送。"
        ),
    }


@router.post("/categories/review/deepseek-suggestions")
def create_deepseek_suggestions(
    body: ModelCategoryBatchRequest, request: Request,
    user: User = Depends(current_user), session: Session = Depends(get_session),
):
    settings = request.app.state.settings
    if not settings.deepseek_enabled or not settings.deepseek_api_key:
        raise BusinessError(
            "DEEPSEEK_NOT_CONFIGURED",
            "DeepSeek分类未启用，请先在本地环境变量中配置密钥并重启后端。",
            503,
        )
    private = PrivateCategoryReviewQueue().load(user.id)
    queue_items = [item for item in private.get("items", []) if item.get("tier") == body.tier]
    other_id = session.scalar(select(Category.id).where(Category.code == "other"))
    contexts: list[ClassificationContext] = []
    selected_items: list[dict] = []
    skipped_private = 0
    for item in queue_items:
        if len(contexts) >= body.limit:
            break
        transaction = session.get(Transaction, item.get("representative_transaction_id"))
        if (
            transaction is None or transaction.user_id != user.id
            or transaction.flow_type != "consumption"
            or transaction.user_category_id is not None
            or transaction.suggested_category_id != other_id
        ):
            continue
        context = ClassificationContext(
            transaction_id=transaction.id,
            source_type_code=transaction.source_type_code,
            occurred_at_utc=transaction.occurred_at_utc,
            amount_minor=transaction.amount_minor,
            counterparty_hint=str(item.get("merchant_hint") or ""),
            description_hint=str(item.get("description_hint") or ""),
            recurring_count=int(item.get("transaction_count") or 1),
        )
        if not is_cloud_eligible(privacy_minimized_features(context, user.timezone)):
            skipped_private += 1
            continue
        contexts.append(context); selected_items.append(item)
    if not contexts:
        return {"created": 0, "skipped_private": skipped_private, "items": []}
    web_search_used = 0
    if settings.merchant_search_enabled and settings.merchant_search_api_key:
        search_provider = VolcengineMerchantSearchProvider(
            api_key=settings.merchant_search_api_key,
            base_url=settings.merchant_search_base_url,
        )
        enriched_contexts: list[ClassificationContext] = []
        for context in contexts:
            try:
                evidence = search_provider.search(context.counterparty_hint)
            except MerchantSearchError:
                enriched_contexts.append(context)
                continue
            if evidence.summary:
                web_search_used += 1
                enriched_contexts.append(replace(
                    context, public_web_evidence=evidence.summary,
                ))
            else:
                enriched_contexts.append(context)
        contexts = enriched_contexts
    provider = DeepSeekClassificationProvider(
        api_key=settings.deepseek_api_key, model_name=settings.deepseek_model,
        base_url=settings.deepseek_base_url, timezone_name=user.timezone,
    )
    try:
        results = provider.classify_many(contexts)
    except ClassificationProviderError as exc:
        code = str(exc)
        if code == "upstream_http_401":
            message = "DeepSeek API密钥无效。"
        elif code == "upstream_http_402":
            message = "DeepSeek API余额不足。"
        elif code == "upstream_http_429":
            message = "DeepSeek API请求过于频繁，请稍后重试。"
        else:
            message = "DeepSeek暂时无法返回可验证的分类结果。"
        raise BusinessError("DEEPSEEK_REQUEST_FAILED", message, 502) from exc
    service = HybridClassificationService(session)
    output = []
    for context, queue_item, result in zip(contexts, selected_items, results, strict=True):
        suggestion_id = service.record_model_suggestion(
            user_id=user.id, transaction_id=context.transaction_id,
            provider=provider.provider_name, model_name=provider.model_name,
            prompt_version=DEEPSEEK_PROMPT_VERSION, result=result,
        )
        output.append({
            "suggestion_id": suggestion_id, "group_id": queue_item.get("group_id"),
            "transaction_id": context.transaction_id,
            "category_code": result.category_code,
            "confidence": result.confidence_bps / 100,
            "evidence_codes": list(result.evidence_codes),
            "requires_review": True,
        })
    session.commit()
    return {
        "created": len(output), "skipped_private": skipped_private,
        "web_search_used": web_search_used, "items": output,
    }


@router.post("/categories/suggestions/{suggestion_id}/accept")
def accept_category_suggestion(
    suggestion_id: int, user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    suggestion = _owned(
        session, ClassificationSuggestion, suggestion_id, user.id, "SUGGESTION_NOT_FOUND",
    )
    category_code = session.scalar(select(Category.code).where(
        Category.id == suggestion.suggested_category_id,
    ))
    if suggestion.status != "pending":
        raise BusinessError("SUGGESTION_ALREADY_REVIEWED", "该建议已完成审核。", 409)
    if category_code == "other":
        raise BusinessError("OTHER_CANNOT_BE_ACCEPTED", "模型建议保留OTHER，无需应用规则。", 422)
    changed = HybridClassificationService(session).accept_suggestion(
        suggestion.id, scope="counterparty",
    )
    session.commit()
    return {"updated_transactions": changed, "category_code": category_code,
            "user_confirmed": True}


@router.post("/categories/suggestions/{suggestion_id}/reject")
def reject_category_suggestion(
    suggestion_id: int, user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    suggestion = _owned(
        session, ClassificationSuggestion, suggestion_id, user.id, "SUGGESTION_NOT_FOUND",
    )
    if suggestion.status != "pending":
        raise BusinessError("SUGGESTION_ALREADY_REVIEWED", "该建议已完成审核。", 409)
    HybridClassificationService(session).reject_suggestion(suggestion.id)
    session.commit()
    return {"status": "rejected"}


@router.get("/analytics/overview")
def analytics(user: User = Depends(current_user), session: Session = Depends(get_session)):
    return WebFinanceService(session, user.id).analytics()


@router.post("/goals")
def create_goal(body: GoalCreate, user: User = Depends(current_user), session: Session = Depends(get_session)):
    snapshot = FinancialSnapshot(user_id=user.id, as_of_date=body.start_date,
                                 available_savings_minor=body.current_savings_minor,
                                 source="user_input", currency="CNY")
    session.add(snapshot); session.flush()
    goal = SavingGoal(user_id=user.id, baseline_snapshot_id=snapshot.id,
                      start_date=body.start_date, target_date=body.target_date,
                      target_savings_minor=body.target_savings_minor,
                      currency="CNY", status="active", version=1)
    session.add(goal); session.flush()
    for item in body.income_assumptions:
        session.add(IncomeAssumption(goal_id=goal.id, **item.model_dump(), version=1, is_active=True))
    session.commit()
    return {"id": goal.id, "snapshot_id": snapshot.id, "status": goal.status}


@router.get("/goals")
def goals(user: User = Depends(current_user), session: Session = Depends(get_session)):
    rows = session.scalars(select(SavingGoal).where(SavingGoal.user_id == user.id)
                           .order_by(SavingGoal.created_at.desc())).all()
    return {"items": [{"id": row.id, "target_savings_minor": row.target_savings_minor,
                       "target_date": row.target_date.isoformat(), "status": row.status,
                       "version": row.version} for row in rows]}


@router.put("/goals/{goal_id}/constraints")
def set_constraints(
    goal_id: int, body: ConstraintSet, user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    goal = _owned(session, SavingGoal, goal_id, user.id, "GOAL_NOT_FOUND")
    categories = CategoryRepository(session).ensure_defaults()
    session.query(BudgetConstraint).filter(BudgetConstraint.goal_id == goal.id).delete()
    for item in body.constraints:
        if item.category_code not in categories:
            raise BusinessError("INVALID_CATEGORY", f"未知类别：{item.category_code}", 422)
        adjustability = 0 if item.is_fixed else ADJUSTABILITY_BPS[item.adjustability]
        session.add(BudgetConstraint(
            goal_id=goal.id, category_id=categories[item.category_code].id,
            minimum_budget_minor=item.minimum_budget_minor,
            maximum_budget_minor=item.maximum_budget_minor,
            is_fixed=item.is_fixed, adjustability_bps=adjustability,
            effective_from=goal.start_date, effective_to=goal.target_date, version=1,
        ))
    session.commit(); return {"goal_id": goal.id, "count": len(body.constraints)}


@router.get("/forecasts/weekly")
def forecast(goal_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    _owned(session, SavingGoal, goal_id, user.id, "GOAL_NOT_FOUND")
    try:
        items = WebFinanceService(session, user.id).forecast_for_goal(goal_id)
    except ValueError as exc:
        raise BusinessError("INSUFFICIENT_HISTORY", "历史数据不足或尚未设置预算约束。", 422,
                            {"reason": str(exc)}) from exc
    return {"model": "total-weekly-forecast-v1", "interval_level": .8,
            "notice": "预测用于估计正常消费趋势，不代表实际消费承诺。",
            "items": [{"week_start": item.week_start.isoformat(), "target_key": item.target_key,
                       "predicted_minor": item.predicted_minor, "lower_minor": item.lower_minor,
                       "upper_minor": item.upper_minor} for item in items]}


@router.post("/budgets/generate")
def generate_budget(
    body: BudgetGenerateRequest, user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    _owned(session, SavingGoal, body.goal_id, user.id, "GOAL_NOT_FOUND")
    try:
        service = WebFinanceService(session, user.id)
        result, _feasibility = service.optimize(body.goal_id, body.adjustment_mode)
        plan = service.save_initial_plan(body.goal_id, result)
        session.commit()
    except ValueError as exc:
        session.rollback()
        raise BusinessError("INVALID_PLANNING_INPUT", "目标、预测或约束尚未准备完成。", 422,
                            {"reason": str(exc)}) from exc
    response = serialize_optimization(result, plan.id)
    response["monthly_plan"] = service.monthly_summary(body.goal_id, result) if result.feasible else []
    return response


@router.post("/budgets/{plan_id}/activate")
def activate_plan(plan_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    plan = _owned(session, BudgetPlan, plan_id, user.id, "PLAN_NOT_FOUND")
    if not plan.is_feasible:
        raise BusinessError("INFEASIBLE_GOAL", "不可行方案不能激活。", 409)
    session.query(BudgetPlan).filter(BudgetPlan.goal_id == plan.goal_id,
                                     BudgetPlan.is_current.is_(True)).update(
        {"is_current": False, "status": "superseded"}
    )
    plan.is_current = True; plan.status = "active"; session.commit()
    return {"plan_id": plan.id, "status": plan.status}


def _plan_dict(session: Session, plan: BudgetPlan) -> dict:
    rows = session.execute(select(BudgetPlanItemModel, Category.code, Category.display_name)
                           .join(Category, Category.id == BudgetPlanItemModel.category_id)
                           .where(BudgetPlanItemModel.budget_plan_id == plan.id)
                           .order_by(BudgetPlanItemModel.week_start, Category.sort_order)).all()
    event_types = session.scalars(
        select(FinancialEvent.event_type)
        .join(BudgetPlanEvent, BudgetPlanEvent.financial_event_id == FinancialEvent.id)
        .where(BudgetPlanEvent.budget_plan_id == plan.id)
        .order_by(FinancialEvent.event_date, FinancialEvent.id)
    ).all()
    return {
        "id": plan.id, "goal_id": plan.goal_id, "version": plan.version,
        "parent_plan_id": plan.parent_plan_id, "status": plan.status,
        "is_current": plan.is_current, "is_feasible": plan.is_feasible,
        "risk_level": plan.risk_level, "adjustment_mode": plan.adjustment_mode,
        "expected_final_savings_minor": plan.expected_final_savings_minor,
        "maximum_achievable_target_minor": plan.maximum_achievable_target_minor,
        "required_additional_income_minor": plan.required_additional_income_minor,
        "recommended_deadline": plan.recommended_deadline.isoformat() if plan.recommended_deadline else None,
        "created_at": plan.created_at.isoformat(),
        "generation_reason": ("initial_plan" if plan.parent_plan_id is None else
                              event_types[0] if event_types else "manual_replan"),
        "items": [{"week_start": item.week_start.isoformat(), "category_code": code,
                   "category_name": name, "natural_spend_minor": item.natural_spend_minor,
                   "recommended_budget_minor": item.recommended_budget_minor,
                   "adjustment_minor": item.adjustment_minor} for item, code, name in rows],
    }


@router.get("/budgets")
def list_budgets(user: User = Depends(current_user), session: Session = Depends(get_session)):
    rows = session.scalars(select(BudgetPlan).where(BudgetPlan.user_id == user.id)
                           .order_by(BudgetPlan.created_at.desc())).all()
    return {"items": [_plan_dict(session, row) | {"items": []} for row in rows]}


@router.get("/budgets/{plan_id}")
def budget_detail(plan_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return _plan_dict(session, _owned(session, BudgetPlan, plan_id, user.id, "PLAN_NOT_FOUND"))


@router.get("/budgets/{plan_id}/execution")
def budget_execution(
    plan_id: int, user: User = Depends(current_user), session: Session = Depends(get_session),
):
    plan = _owned(session, BudgetPlan, plan_id, user.id, "PLAN_NOT_FOUND")
    service = WebFinanceService(session, user.id)
    version = service.plan_version(plan)
    actuals = service.historical_actuals()
    actual_weeks = sorted({item.week_start for item in actuals if item.target_key == "total"})
    reports = []
    for week in actual_weeks:
        if not (plan.plan_start_week <= week <= plan.plan_end_week):
            continue
        report = calculate_weekly_variance(version.items, actuals, week)
        trigger = choose_replan_trigger(report, ())
        state = ("replan_recommended" if trigger else
                 "minor_variance" if report.absolute_variance_minor > 0 else "normal")
        reports.append({
            "week_start": week.isoformat(), "status": state,
            "trigger_source": trigger.value if trigger else None,
            "budget_minor": report.budget_minor, "actual_minor": report.actual_minor,
            "absolute_variance_minor": report.absolute_variance_minor,
            "percentage_variance_bps": report.percentage_variance_bps,
            "categories": [asdict(item) for item in report.category_variances],
        })
    return {"items": reports, "threshold_note": "再规划建议由后端统一偏差规则计算。"}


EVENT_SCOPE = {
    "planned_overrun": ImpactScope.ACTUAL_SPEND,
    "unplanned_expense": ImpactScope.ACTUAL_SPEND,
    "extra_income": ImpactScope.BALANCE,
    "income_decrease": ImpactScope.FUTURE_INCOME,
    "income_increase": ImpactScope.FUTURE_INCOME,
    "goal_amount_change": ImpactScope.GOAL,
    "goal_date_change": ImpactScope.GOAL,
    "budget_constraint_change": ImpactScope.CONSTRAINT,
    "manual_replan": ImpactScope.ACTUAL_SPEND,
}


@router.post("/events")
def create_event(body: EventCreate, user: User = Depends(current_user), session: Session = Depends(get_session)):
    _owned(session, SavingGoal, body.goal_id, user.id, "GOAL_NOT_FOUND")
    try:
        event_type = FinancialEventType(body.event_type); scope = EVENT_SCOPE[body.event_type]
    except (ValueError, KeyError) as exc:
        raise BusinessError("INVALID_EVENT_TYPE", "不支持的财务事件类型。", 422) from exc
    item = FinancialEvent(
        user_id=user.id, goal_id=body.goal_id, event_type=event_type.value,
        amount_minor=body.amount_minor, event_date=body.event_date,
        impact_scope=scope.value, reason_code="USER_REPORTED",
        safe_note=body.safe_note, deduplication_key=str(uuid4()),
    )
    session.add(item); session.commit()
    return {"id": item.id, "event_type": item.event_type, "status": "recorded"}


def _compute_replan(session: Session, user: User, plan: BudgetPlan, body: ReplanRequestBody):
    service = WebFinanceService(session, user.id)
    _goal_row, goal, _old_snapshot, incomes, constraints = service.goal_objects(plan.goal_id)
    event_rows = session.scalars(select(FinancialEvent).where(
        FinancialEvent.id.in_(body.event_ids or [-1]), FinancialEvent.user_id == user.id,
        FinancialEvent.goal_id == plan.goal_id,
    )).all()
    if len(event_rows) != len(set(body.event_ids)):
        raise BusinessError("EVENT_NOT_FOUND", "一个或多个事件不存在。", 404)
    event_specs = tuple(FinancialEventSpec(
        item.id, item.user_id, item.goal_id, FinancialEventType(item.event_type),
        item.amount_minor, item.event_date, ImpactScope(item.impact_scope),
        item.reason_code, item.safe_note,
    ) for item in event_rows)
    snapshot_id = (session.scalar(select(func.max(FinancialSnapshot.id))) or 0) + 1
    snapshot = FinancialSnapshotSpec(snapshot_id, user.id, body.next_open_week,
                                     body.current_savings_minor, "user_input")
    actuals = service.historical_actuals(body.next_open_week)
    weeks = planning_weeks(body.next_open_week, goal.target_date)
    categories = sorted({item.category_code for item in constraints})
    predictions = service.forecast_for_goal(plan.goal_id, cutoff=body.next_open_week)
    constraints = service.effective_constraints(constraints, weeks)
    request = ReplanRequest(
        service.plan_version(plan), actuals, event_specs, snapshot, goal, constraints,
        predictions, body.next_open_week, BudgetStrategy.PERSONALIZED,
        AdjustmentMode(body.adjustment_mode), incomes, body.recovery_horizon_weeks,
        ReplanTriggerSource.EVENT_REQUIRED if event_specs else ReplanTriggerSource.USER_REQUESTED,
    )
    return RollingBudgetReplanner().replan(request), snapshot, goal, event_specs


@router.post("/budgets/{plan_id}/replan-preview")
def preview_replan(
    plan_id: int, body: ReplanRequestBody, user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    plan = _owned(session, BudgetPlan, plan_id, user.id, "PLAN_NOT_FOUND")
    if not plan.is_current:
        raise BusinessError("PLAN_VERSION_CONFLICT", "只能基于当前有效计划重新规划。", 409)
    result, _snapshot, _goal, _events = _compute_replan(session, user, plan, body)
    response = serialize_optimization(result.optimization_result)
    response.update({"status_code": result.status_code, "parent_plan_id": plan.id,
                     "new_version": result.new_version,
                     "difference": asdict(result.difference) if result.difference else None,
                     "recovery_metrics": asdict(result.recovery_metrics) if result.recovery_metrics else None})
    return response


@router.post("/budgets/{plan_id}/replans")
def confirm_replan(
    plan_id: int, body: ReplanRequestBody, user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    plan = _owned(session, BudgetPlan, plan_id, user.id, "PLAN_NOT_FOUND")
    if not plan.is_current:
        raise BusinessError("PLAN_VERSION_CONFLICT", "计划版本已变化，请刷新后重试。", 409)
    result, snapshot, goal, events = _compute_replan(session, user, plan, body)
    # The persistence service owns the complete atomic transaction. Close the
    # read-only SQLAlchemy autobegin opened while preparing the candidate.
    session.rollback()
    try:
        new_id = ReplanPersistenceService().save(session, PersistReplanCommand(
            plan_id, snapshot, goal, events, result,
            activate=True, events_already_persisted=True,
        ))
    except ValueError as exc:
        raise BusinessError("REPLAN_CONFLICT", "重新规划数据已变化，请刷新后重试。", 409,
                            {"reason": str(exc)}) from exc
    return {"plan_id": new_id, "status_code": result.status_code,
            "feasible": result.optimization_result.feasible}


@router.get("/dashboard")
def dashboard(user: User = Depends(current_user), session: Session = Depends(get_session)):
    goal = session.scalar(select(SavingGoal).where(SavingGoal.user_id == user.id,
                                                   SavingGoal.status == "active")
                          .order_by(SavingGoal.created_at.desc()))
    plan = session.scalar(select(BudgetPlan).where(BudgetPlan.user_id == user.id,
                                                   BudgetPlan.is_current.is_(True)))
    analytics_result = WebFinanceService(session, user.id).analytics()
    snapshot = session.scalar(select(FinancialSnapshot).where(
        FinancialSnapshot.user_id == user.id
    ).order_by(FinancialSnapshot.as_of_date.desc(), FinancialSnapshot.id.desc()))
    current_month = date.today().strftime("%Y-%m")
    today = date.today(); current_week = today - timedelta(days=today.weekday())
    month = next((item for item in analytics_result.get("monthly", [])
                  if item["year_month"] == current_month), None)
    week = next((item for item in analytics_result.get("weekly", [])
                 if item["week_start"] == current_week.isoformat()), None)
    budget_minor = 0
    if plan:
        budget_minor = session.scalar(select(func.sum(BudgetPlanItemModel.recommended_budget_minor)).where(
            BudgetPlanItemModel.budget_plan_id == plan.id,
            BudgetPlanItemModel.week_start == current_week,
        )) or 0
    actual_minor = int(week["net_consumption_minor"]) if week else 0
    monthly_income_minor = 0
    if goal:
        incomes = session.scalars(select(IncomeAssumption).where(
            IncomeAssumption.goal_id == goal.id, IncomeAssumption.is_active.is_(True),
            IncomeAssumption.start_date <= today, IncomeAssumption.end_date >= date(today.year, today.month, 1),
        )).all()
        for income in incomes:
            if income.frequency == "monthly":
                monthly_income_minor += income.amount_minor
            elif income.frequency == "one_time" and income.start_date.strftime("%Y-%m") == current_month:
                monthly_income_minor += income.amount_minor
            elif income.frequency == "weekly":
                monthly_income_minor += income.amount_minor * 4
    target = goal.target_savings_minor if goal else 0
    savings = snapshot.available_savings_minor if snapshot else None
    constraint_count = (session.scalar(select(func.count(BudgetConstraint.id)).where(
        BudgetConstraint.goal_id == goal.id
    )) or 0) if goal else 0
    if not analytics_result["has_data"]:
        next_action = {"key": "import", "title": "导入第一份账单", "description": "先建立真实消费基础。", "path": "/bills"}
    elif not goal:
        next_action = {"key": "goal", "title": "设置储蓄目标", "description": "填写存款、收入和目标日期。", "path": "/goals"}
    elif not constraint_count:
        next_action = {"key": "constraints", "title": "设置预算底线", "description": "说明哪些开支必须保留、哪些可调整。", "path": "/goals"}
    elif not plan:
        next_action = {"key": "budget", "title": "生成预算方案", "description": "比较正常消费与目标所需预算。", "path": "/budgets"}
    else:
        next_action = {"key": "execute", "title": "查看本周执行", "description": "有财务变化时再重新规划。", "path": "/replan"}
    return {
        "has_data": analytics_result["has_data"],
        "current_savings_minor": savings,
        "monthly_income_assumption_minor": monthly_income_minor if goal else None,
        "monthly_actual_minor": month["net_consumption_minor"] if month else None,
        "goal_progress_bps": (min(10_000, round(savings / target * 10_000))
                              if savings is not None and target else None),
        "current_week": {"week_start": current_week.isoformat(), "budget_minor": budget_minor,
                         "actual_minor": actual_minor,
                         "usage_bps": round(actual_minor / budget_minor * 10_000) if budget_minor else None,
                         "status": ("replan_recommended" if budget_minor and actual_minor > budget_minor
                                    else "normal" if budget_minor else "no_plan")},
        "goal": ({"id": goal.id, "target_savings_minor": goal.target_savings_minor,
                  "target_date": goal.target_date.isoformat()} if goal else None),
        "plan": ({"id": plan.id, "version": plan.version, "risk_level": plan.risk_level,
                  "expected_final_savings_minor": plan.expected_final_savings_minor,
                  "last_replan_status": ("已激活新方案" if plan.parent_plan_id else "初始方案")}
                 if plan else None),
        "recent_weekly": analytics_result.get("weekly", [])[-8:],
        "workflow": {
            "has_transactions": analytics_result["has_data"],
            "has_goal": goal is not None,
            "has_constraints": bool(constraint_count),
            "has_active_plan": plan is not None,
            "constraint_count": constraint_count,
            "next_action": next_action,
        },
    }
