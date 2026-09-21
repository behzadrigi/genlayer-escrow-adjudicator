# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

import json

from genlayer import *
from dataclasses import dataclass


@allow_storage
@dataclass
class Escrow:
    escrow_id: u256
    buyer: str
    seller: str
    amount: u256
    terms_hash: str
    status: str
    dispute_contract: str
    deposit_contract: str


class EscrowFactory(gl.Contract):
    escrows: TreeMap[u256, Escrow]
    next_id: u256
    deployer: str
    deposit_vault: str
    dispute_resolver: str
    downstream_set: bool

    def __init__(self):
        self.next_id = u256(0)
        self.deployer = str(gl.message.sender_address)
        self.deposit_vault = ""
        self.dispute_resolver = ""
        self.downstream_set = False

    @gl.public.write
    def set_downstream_contracts(self, deposit_vault: str, dispute_resolver: str):
        assert str(gl.message.sender_address) == self.deployer, "Only deployer can set downstream contracts"
        assert not self.downstream_set, "Downstream contracts already set"
        assert deposit_vault.strip() != "", "Deposit vault address cannot be empty"
        assert dispute_resolver.strip() != "", "Dispute resolver address cannot be empty"
        self.deposit_vault = deposit_vault
        self.dispute_resolver = dispute_resolver
        self.downstream_set = True

    @gl.public.write
    def create_escrow(self, seller: str, terms_hash: str, amount: u256) -> u256:
        buyer = str(gl.message.sender_address)

        assert seller.strip() != "", "Seller address cannot be empty"
        assert seller != buyer, "Buyer and seller cannot be the same"
        assert terms_hash.strip() != "", "Terms hash cannot be empty"
        assert amount > u256(0), "Amount must be greater than zero"

        eid = self.next_id
        self.next_id += u256(1)

        self.escrows[eid] = Escrow(
            escrow_id=eid,
            buyer=buyer,
            seller=seller,
            amount=amount,
            terms_hash=terms_hash,
            status="CREATED",
            dispute_contract="",
            deposit_contract="",
        )

        return eid

    @gl.public.write
    def cancel_escrow(self, escrow_id: u256):
        assert escrow_id in self.escrows, "Escrow not found"
        esc = self.escrows[escrow_id]

        caller = str(gl.message.sender_address)
        assert caller == esc.buyer, "Only buyer can cancel the escrow"
        assert esc.status == "CREATED", "Escrow can only be cancelled before deposit"

        esc.status = "CANCELLED"
        self.escrows[escrow_id] = esc

        return True

    @gl.public.write
    def mark_funded(self, escrow_id: u256, deposit_contract: str):
        assert escrow_id in self.escrows, "Escrow not found"
        assert self.downstream_set, "Downstream contracts not set"
        caller = str(gl.message.sender_address)
        assert caller == self.deposit_vault, "Only deposit vault can mark as funded"

        esc = self.escrows[escrow_id]
        assert esc.status == "CREATED", "Escrow must be in CREATED status"
        esc.status = "FUNDED"
        esc.deposit_contract = deposit_contract
        self.escrows[escrow_id] = esc

        return True

    @gl.public.write
    def mark_disputed(self, escrow_id: u256, dispute_contract: str):
        assert escrow_id in self.escrows, "Escrow not found"
        assert self.downstream_set, "Downstream contracts not set"
        caller = str(gl.message.sender_address)
        assert caller == self.dispute_resolver, "Only dispute resolver can mark as disputed"

        esc = self.escrows[escrow_id]
        assert esc.status == "FUNDED", "Escrow must be in FUNDED status"
        esc.status = "DISPUTED"
        esc.dispute_contract = dispute_contract
        self.escrows[escrow_id] = esc

        return True

    @gl.public.write
    def mark_settled(self, escrow_id: u256):
        assert escrow_id in self.escrows, "Escrow not found"
        assert self.downstream_set, "Downstream contracts not set"
        caller = str(gl.message.sender_address)
        assert caller == self.deposit_vault, "Only deposit vault can mark as settled"

        esc = self.escrows[escrow_id]
        assert esc.status == "DISPUTED", "Escrow must be in DISPUTED status"
        esc.status = "SETTLED"
        self.escrows[escrow_id] = esc

        return True

    @gl.public.write
    def mark_refunded(self, escrow_id: u256):
        assert escrow_id in self.escrows, "Escrow not found"
        assert self.downstream_set, "Downstream contracts not set"
        caller = str(gl.message.sender_address)
        assert caller == self.deposit_vault, "Only deposit vault can mark as refunded"

        esc = self.escrows[escrow_id]
        assert esc.status in ("CREATED", "FUNDED", "DISPUTED"), "Invalid status for refund"
        esc.status = "REFUNDED"
        self.escrows[escrow_id] = esc

        return True

    @gl.public.view
    def get_escrow_status(self, escrow_id: u256) -> str:
        if escrow_id not in self.escrows:
            return "NOT_FOUND"
        esc = self.escrows[escrow_id]
        return esc.status

    @gl.public.view
    def get_escrow_details(self, escrow_id: u256) -> str:
        if escrow_id not in self.escrows:
            return "NOT_FOUND"
        esc = self.escrows[escrow_id]
        return json.dumps({
            "escrow_id": int(esc.escrow_id),
            "buyer": esc.buyer,
            "seller": esc.seller,
            "amount": int(esc.amount),
            "terms_hash": esc.terms_hash,
            "status": esc.status,
            "dispute_contract": esc.dispute_contract,
            "deposit_contract": esc.deposit_contract,
        })

    @gl.public.view
    def get_escrow_data(self, escrow_id: u256) -> str:
        if escrow_id not in self.escrows:
            return "NOT_FOUND"
        esc = self.escrows[escrow_id]
        return json.dumps({
            "escrow_id": int(esc.escrow_id),
            "buyer": esc.buyer,
            "seller": esc.seller,
            "amount": int(esc.amount),
            "terms_hash": esc.terms_hash,
            "status": esc.status,
        })

    @gl.public.view
    def list_user_escrows(self, user: str) -> str:
        items = []
        for key in self.escrows:
            esc = self.escrows[key]
            if esc.buyer == user or esc.seller == user:
                items.append(str(int(esc.escrow_id)))
        return ",".join(items)

    @gl.public.view
    def list_all_escrows(self) -> str:
        items = []
        for key in self.escrows:
            esc = self.escrows[key]
            items.append(f"{int(esc.escrow_id)}:{esc.status}")
        return ",".join(items)

    @gl.public.view
    def get_deployer(self) -> str:
        return self.deployer

    @gl.public.view
    def get_downstream_contracts(self) -> str:
        return json.dumps({
            "deposit_vault": self.deposit_vault,
            "dispute_resolver": self.dispute_resolver,
            "downstream_set": self.downstream_set,
        })
