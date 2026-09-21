# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

import json

from genlayer import *
from dataclasses import dataclass


@allow_storage
@dataclass
class Settlement:
    settlement_id: u256
    judgment_id: u256
    dispute_id: u256
    escrow_id: u256
    amount_to_seller: u256
    amount_to_buyer: u256
    status: str


class SettlementEngine(gl.Contract):
    settlements: TreeMap[u256, Settlement]
    applied_judgments: TreeMap[u256, bool]
    next_id: u256
    deposit_contract: str
    resolver_contract: str
    factory_contract: str

    def __init__(self, deposit_address: str, resolver_address: str, factory_address: str):
        self.next_id = u256(0)
        self.deposit_contract = deposit_address
        self.resolver_contract = resolver_address
        self.factory_contract = factory_address

    @gl.public.write
    def settle(self, judgment_id: u256) -> u256:
        assert judgment_id not in self.applied_judgments, "Judgment already applied"

        # Read judgment from resolver on-chain
        judgment_raw = gl.get_contract_at(
            Address(self.resolver_contract)
        ).view().get_judgment_data(judgment_id)

        assert judgment_raw != "NOT_FOUND", "Judgment not found"

        try:
            judgment_data = json.loads(judgment_raw)
        except:
            raise gl.vm.UserError("Invalid judgment data from resolver")

        status = judgment_data.get("status", "")
        assert status == "FINAL", "Judgment is not final"

        verdict = judgment_data.get("verdict", "")
        ratio = judgment_data.get("compensation_ratio", 0)
        dispute_id = judgment_data.get("dispute_id", 0)

        assert verdict in ("REFUND_TO_BUYER", "RELEASE_TO_SELLER", "SPLIT"), "Invalid verdict"
        assert 0 <= ratio <= 100, "Invalid compensation ratio"

        # Read dispute to get escrow_id
        dispute_raw = gl.get_contract_at(
            Address(self.resolver_contract)
        ).view().get_dispute_data(dispute_id)

        assert dispute_raw != "NOT_FOUND", "Dispute not found"

        try:
            dispute_data = json.loads(dispute_raw)
        except:
            raise gl.vm.UserError("Invalid dispute data from resolver")

        escrow_id = dispute_data.get("escrow_id", 0)

        # Read deposit for this escrow
        deposit_id_str = gl.get_contract_at(
            Address(self.deposit_contract)
        ).view().get_deposit_for_escrow(escrow_id)

        assert deposit_id_str != "NOT_FOUND", "Deposit not found for escrow"

        try:
            deposit_id = int(deposit_id_str)
        except:
            raise gl.vm.UserError("Invalid deposit id from deposit vault")

        # Read deposit data
        deposit_raw = gl.get_contract_at(
            Address(self.deposit_contract)
        ).view().get_deposit_data(deposit_id)

        assert deposit_raw != "NOT_FOUND", "Deposit data not found"

        try:
            deposit_data = json.loads(deposit_raw)
        except:
            raise gl.vm.UserError("Invalid deposit data from deposit vault")

        total_amount = int(deposit_data.get("amount", 0))
        deposit_status = deposit_data.get("status", "")

        assert deposit_status == "LOCKED", "Deposit is not locked"
        assert total_amount > 0, "Deposit amount must be greater than zero"

        # Compute split based on ratio
        amount_to_seller = int((total_amount * ratio) // 100)
        amount_to_buyer = total_amount - amount_to_seller

        # Trigger release or refund on DepositVault via write-to-write
        if ratio == 100:
            gl.get_contract_at(
                Address(self.deposit_contract)
            ).emit().release_to_seller(
                u256(deposit_id),
                judgment_id,
                self.resolver_contract,
            )
        elif ratio == 0:
            gl.get_contract_at(
                Address(self.deposit_contract)
            ).emit().refund_to_buyer(
                u256(deposit_id),
                judgment_id,
                self.resolver_contract,
            )
        else:
            # SPLIT: release proportional amount
            gl.get_contract_at(
                Address(self.deposit_contract)
            ).emit().release_to_seller(
                u256(deposit_id),
                judgment_id,
                self.resolver_contract,
            )

        sid = self.next_id
        self.next_id += u256(1)

        self.settlements[sid] = Settlement(
            settlement_id=sid,
            judgment_id=judgment_id,
            dispute_id=u256(dispute_id),
            escrow_id=u256(escrow_id),
            amount_to_seller=u256(amount_to_seller),
            amount_to_buyer=u256(amount_to_buyer),
            status="APPLIED",
        )

        self.applied_judgments[judgment_id] = True

        return sid

    @gl.public.view
    def get_settlement_details(self, settlement_id: u256) -> str:
        if settlement_id not in self.settlements:
            return "NOT_FOUND"
        s = self.settlements[settlement_id]
        return json.dumps({
            "settlement_id": int(s.settlement_id),
            "judgment_id": int(s.judgment_id),
            "dispute_id": int(s.dispute_id),
            "escrow_id": int(s.escrow_id),
            "amount_to_seller": int(s.amount_to_seller),
            "amount_to_buyer": int(s.amount_to_buyer),
            "status": s.status,
        })

    @gl.public.view
    def is_judgment_applied(self, judgment_id: u256) -> str:
        if judgment_id in self.applied_judgments:
            return "APPLIED"
        return "NOT_APPLIED"

    @gl.public.view
    def list_settlements(self) -> str:
        items = []
        for key in self.settlements:
            s = self.settlements[key]
            items.append(f"{int(s.settlement_id)}:{s.status}")
        return ",".join(items)

    @gl.public.view
    def get_deposit_contract(self) -> str:
        return self.deposit_contract

    @gl.public.view
    def get_resolver_contract(self) -> str:
        return self.resolver_contract

    @gl.public.view
    def get_factory_contract(self) -> str:
        return self.factory_contract
