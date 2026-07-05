from sqlalchemy.orm import Session

from app.models import Transaction, TransactionTypeEnum, Wallet
from app.utils.money import to_money
from app.utils.time import to_utc_naive, utc_now


class FinanceService:
    @staticmethod
    def create_wallet(db: Session, user_id, name, wallet_type, balance: float = 0.0):
        wallet = Wallet(user_id=user_id, name=name, wallet_type=wallet_type, balance=to_money(balance))
        db.add(wallet)
        db.commit()
        db.refresh(wallet)
        return wallet

    @staticmethod
    def add_transaction(db: Session, wallet_id, data):
        wallet = db.query(Wallet).filter(Wallet.id == wallet_id, Wallet.deleted_at.is_(None)).first()
        if not wallet:
            return None
        transaction = Transaction(
            wallet_id=wallet_id,
            type=data.type,
            amount=to_money(data.amount),
            category=data.category,
            transaction_date=to_utc_naive(data.transaction_date) or utc_now(),
            description=data.description,
            is_private=data.is_private,
        )
        if data.type == TransactionTypeEnum.income:
            wallet.balance = to_money(wallet.balance) + to_money(data.amount)
        else:
            wallet.balance = to_money(wallet.balance) - to_money(data.amount)
        wallet.balance = to_money(wallet.balance)
        wallet.updated_at = utc_now()
        db.add(transaction)
        db.commit()
        db.refresh(transaction)
        return transaction
