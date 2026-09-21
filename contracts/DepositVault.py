# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

import json

from genlayer import *
from dataclasses import dataclass


@allow_storage
@dataclass
class Deposit:
    deposit_id: u256
    escrow_id: u256
    depositor: str
    amount: u256
    status: str
    released_to: str
    judgment_id: u256


class DepositVault(gl.Contract):
    deposits: TreeMap[u256, Deposit]
    escrow_for_deposit: TreeMap[u256, u256]
    next_id: u256
    factory_contract: str

    def __init__(self, factory_address: str):
        self.next_id = u256(0)
        self.factory_contract = factory_address

    @gl.public.write
    def deposit(self, escrow_id: u256) -> u256:
        amount = u256(gl.message.value)
        assert amount > u256(0), "Deposit amount must be greater than zero"

        escrow_raw = gl.get_contract_at(
            Address(self.factory_contract)
        ).view().get_escrow_data(escrow_id)

        assert escrow_raw != "NOT_FOUND", "Escrow not found in factory"

        try:
            escrow_data = json.loads(escrow_raw)
        except:
            raise gl.vm.UserError("Invalid escrow data from factory")

        buyer = escrow_data.get("buyer", "")
        escrow_amount = escrow_data.get("amount", 0)
        escrow_status = escrow_data.get("status", "")

        caller = str(gl.message.sender_address)
        assert caller == buyer, "Only the buyer can deposit into this escrow"
        assert escrow_status == "CREATED", "Escrow is not in CREATED status"
        assert amount == u256(escrow_amount), "Deposit amount must match escrow amount"

        # Check no duplicate deposit for this escrow
        assert escrow_id not in self.escrow_for_deposit, "Escrow already funded"

        did = self.next_id
        self.next_id += u256(1)

        self.deposits[did] = Deposit(
            deposit_id=did,
            escrow_id=escrow_id,
            depositor=caller,
            amount=amount,
            status="LOCKED",
            released_to="",
            judgment_id=u256(0),
        )
        self.escrow_for_deposit[escrow_id] = did

        # Notify factory that escrow is funded
        gl.get_contract_at(
            Address(self.factory_contract)
        ).emit().mark_funded(escrow_id, str(gl.message.sender_address))

        return did

    @gl.public.write
    def release_to_seller(self, deposit_id: u256, judgment_id: u256, resolver_address: str):
        assert deposit_id in self.deposits, "Deposit not found"
        dep = self.deposits[deposit_id]
        assert dep.status == "LOCKED", "Deposit is not locked"

        # Read judgment from resolver on-chain
        judgment_raw = gl.get_contract_at(
            Address(resolver_address)
        ).view().get_judgment_data(judgment_id)

        assert judgment_raw != "NOT_FOUND", "Judgment not found"

        try:
            judgment_data = json.loads(judgment_raw)
        except:
            raise gl.vm.UserError("Invalid judgment data from resolver")

        verdict = judgment_data.get("verdict", "")
        assert verdict == "RELEASE_TO_SELLER", "Judgment does not authorize release to seller"
        assert judgment_data.get("status", "") == "FINAL", "Judgment is not final"

        # Read escrow to get seller
        escrow_raw = gl.get_contract_at(
            Address(self.factory_contract)
        ).view().get_escrow_data(dep.escrow_id)

        assert escrow_raw != "NOT_FOUND", "Escrow not found in factory"

        try:
            escrow_data = json.loads(escrow_raw)
        except:
            raise gl.vm.UserError("Invalid escrow data from factory")

        seller = escrow_data.get("seller", "")
        assert seller != "", "Seller not found in escrow"

        dep.status = "RELEASED"
        dep.released_to = seller
        dep.judgment_id = judgment_id
        self.deposits[deposit_id] = dep

        gl.get_contract_at(
            Address(self.factory_contract)
        ).emit().mark_settled(dep.escrow_id)

        return True

    @gl.public.write
    def refund_to_buyer(self, deposit_id: u256, judgment_id: u256, resolver_address: str):
        assert deposit_id in self.deposits, "Deposit not found"
        dep = self.deposits[deposit_id]
        assert dep.status == "LOCKED", "Deposit is not locked"

        judgment_raw = gl.get_contract_at(
            Address(resolver_address)
        ).view().get_judgment_data(judgment_id)

        assert judgment_raw != "NOT_FOUND", "Judgment not found"

        try:
            judgment_data = json.loads(judgment_raw)
        except:
            raise gl.vm.UserError("Invalid judgment data from resolver")

        verdict = judgment_data.get("verdict", "")
        assert verdict == "REFUND_TO_BUYER", "Judgment does not authorize refund to buyer"
        assert judgment_data.get("status", "") == "FINAL", "Judgment is not final"

        buyer = dep.depositor
        dep.status = "REFUNDED"
        dep.released_to = buyer
        dep.judgment_id = judgment_id
        self.deposits[deposit_id] = dep

        gl.get_contract_at(
            Address(self.factory_contract)
        ).emit().mark_refunded(dep.escrow_id)

        return True

    @gl.public.write
    def refund_on_cancel(self, escrow_id: u256):
        assert escrow_id in self.escrow_for_deposit, "No deposit for this escrow"
        did = self.escrow_for_deposit[escrow_id]
        dep = self.deposits[did]

        caller = str(gl.message.sender_address)
        assert caller == str(gl.message.sender_address), "Invalid caller"

        escrow_raw = gl.get_contract_at(
            Address(self.factory_contract)
        ).view().get_escrow_data(escrow_id)

        assert escrow_raw != "NOT_FOUND", "Escrow not found"

        try:
            escrow_data = json.loads(escrow_raw)
        except:
            raise gl.vm.UserError("Invalid escrow data from factory")

        assert escrow_data.get("status", "") == "CANCELLED", "Escrow is not cancelled"

        dep.status = "REFUNDED"
        dep.released_to = dep.depositor
        self.deposits[did] = dep

        gl.get_contract_at(
            Address(self.factory_contract)
        ).emit().mark_refunded(escrow_id)

        return True

    @gl.public.view
    def get_deposit_status(self, deposit_id: u256) -> str:
        if deposit_id not in self.deposits:
            return "NOT_FOUND"
        dep = self.deposits[deposit_id]
        return dep.status

    @gl.public.view
    def get_deposit_details(self, deposit_id: u256) -> str:
        if deposit_id not in self.deposits:
            return "NOT_FOUND"
        dep = self.deposits[deposit_id]
        return json.dumps({
            "deposit_id": int(dep.deposit_id),
            "escrow_id": int(dep.escrow_id),
            "depositor": dep.depositor,
            "amount": int(dep.amount),
            "status": dep.status,
            "released_to": dep.released_to,
            "judgment_id": int(dep.judgment_id),
        })

    @gl.public.view
    def get_deposit_data(self, deposit_id: u256) -> str:
        if deposit_id not in self.deposits:
            return "NOT_FOUND"
        dep = self.deposits[deposit_id]
        return json.dumps({
            "deposit_id": int(dep.deposit_id),
            "escrow_id": int(dep.escrow_id),
            "depositor": dep.depositor,
            "amount": int(dep.amount),
            "status": dep.status,
        })

    @gl.public.view
    def get_deposit_for_escrow(self, escrow_id: u256) -> str:
        if escrow_id not in self.escrow_for_deposit:
            return "NOT_FOUND"
        did = self.escrow_for_deposit[escrow_id]
        return str(int(did))

    @gl.public.view
    def list_deposits(self) -> str:
        items = []
        for key in self.deposits:
            dep = self.deposits[key]
            items.append(f"{int(dep.deposit_id)}:{dep.status}")
        return ",".join(items)
