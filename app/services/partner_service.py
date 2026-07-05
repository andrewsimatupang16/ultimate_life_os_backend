from sqlalchemy.orm import Session

from app.models import AccountabilityConnection, ConnectionStatusEnum


class PartnerService:
    @staticmethod
    def send_request(db: Session, requester_id, receiver_id):
        connection = AccountabilityConnection(
            requester_id=requester_id,
            receiver_id=receiver_id,
            status=ConnectionStatusEnum.pending,
        )
        db.add(connection)
        db.commit()
        db.refresh(connection)
        return connection

    @staticmethod
    def accept_request(db: Session, connection_id):
        connection = db.query(AccountabilityConnection).filter(AccountabilityConnection.id == connection_id).first()
        if not connection:
            return None
        connection.status = ConnectionStatusEnum.accepted
        db.commit()
        db.refresh(connection)
        return connection
