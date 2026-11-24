"""T13 体重记录与趋势（FRD F08-1/2）。

录入 0.1–30kg、精度 0.01kg；时间默认现在、可回改任意过去（未来容忍 60s）；
同日多次全部保留。GET 返回最近记录（倒序）与 90 天趋势点＋骤变提示。
"""
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import APIError
from app.core.security import utcnow
from app.models import User, WeightRecord
from app.core.deps import consented_user, owned_pet
from app.services.weights import detect_sudden_change

router = APIRouter(prefix='/api/v1/pets', tags=['weights'])

FUTURE_SKEW = timedelta(seconds=60)
TREND_WINDOW = timedelta(days=90)
CENT = Decimal('0.01')


class WeightInput(BaseModel):
    model_config = {'extra': 'forbid'}

    weight_kg: float
    measured_at: Optional[datetime] = None


def _record_payload(record: WeightRecord) -> dict:
    return {'id': record.id, 'weight_kg': float(record.weight_kg),
            'measured_at': record.measured_at.isoformat()}


@router.post('/{pet_id}/weights', status_code=201)
def create_weight(pet_id: int, body: WeightInput, response: Response,
                  user: User = Depends(consented_user), db: Session = Depends(get_db)):
    pet = owned_pet(pet_id, user, db)
    now = utcnow()
    fields = []
    value = None
    if not math.isfinite(body.weight_kg) or not 0.1 <= body.weight_kg <= 30:
        fields.append({'field': 'weight_kg', 'message': '体重需在 0.1–30kg 之间'})
    else:
        value = Decimal(str(body.weight_kg))
        if value != value.quantize(CENT):
            fields.append({'field': 'weight_kg', 'message': '体重最多保留 2 位小数（精度 0.01kg）'})
    measured_at = body.measured_at
    if measured_at is not None:
        if measured_at.tzinfo is not None:
            measured_at = measured_at.astimezone(timezone.utc).replace(tzinfo=None)
        if measured_at > now + FUTURE_SKEW:
            fields.append({'field': 'measured_at', 'message': '测量时间不能晚于当前时间'})
    else:
        measured_at = now
    if fields:
        raise APIError(400, 'VALIDATION_ERROR', '记录内容校验未通过', {'fields': fields})
    assert value is not None
    record = WeightRecord(pet_id=pet.id, weight_kg=float(value.quantize(CENT)), measured_at=measured_at)
    db.add(record)
    db.commit()
    db.refresh(record)
    response.headers['Cache-Control'] = 'no-store'
    return _record_payload(record)


@router.get('/{pet_id}/weights')
def list_weights(pet_id: int, response: Response,
                 limit: int = Query(50, ge=1, le=200),
                 user: User = Depends(consented_user), db: Session = Depends(get_db)):
    pet = owned_pet(pet_id, user, db)
    response.headers['Cache-Control'] = 'no-store'
    records = db.scalars(select(WeightRecord).where(WeightRecord.pet_id == pet.id)
                         .order_by(WeightRecord.measured_at.desc(), WeightRecord.id.desc()).limit(limit)).all()
    since = utcnow() - TREND_WINDOW
    window = db.scalars(select(WeightRecord).where(WeightRecord.pet_id == pet.id,
                                                   WeightRecord.measured_at >= since)
                        .order_by(WeightRecord.measured_at.asc(), WeightRecord.id.asc())).all()
    points = [{'weight_kg': float(r.weight_kg), 'measured_at': r.measured_at} for r in window]
    return {'records': [_record_payload(r) for r in records], 'count': len(records),
            'trend': {'window_days': 90,
                      'points': [{'weight_kg': p['weight_kg'], 'measured_at': p['measured_at'].isoformat()} for p in points],
                      'alert': detect_sudden_change(points)}}
