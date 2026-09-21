# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

import json
import hashlib

from genlayer import *
from dataclasses import dataclass


def _normalize_url(url: str) -> str:
    if not url:
        return ""
    cleaned = url.strip().rstrip('/')
    if cleaned.startswith('http://'):
        cleaned = cleaned.replace('http://', 'https://', 1)
    cleaned = cleaned.replace(' ', '')
    if '?' in cleaned:
        cleaned = cleaned.split('?')[0]
    return cleaned


def _extract_domain(url: str) -> str:
    try:
        without_protocol = url.replace('https://', '').replace('http://', '', 1)
        domain = without_protocol.split('/')[0]
        if domain.startswith('www.'):
            domain = domain[4:]
        return domain
    except Exception:
        return ""


def _fetch_and_evaluate(url: str, claim: str, side: str) -> dict:
    try:
        content = gl.nondet.web.render(url)
    except:
        return {"url": url, "corroborates": False, "weight": 0}

    prompt = f"""
    Dispute claim from {side}: {claim}

    Evidence URL: {url}
    Content from source:
    {content[:2000]}

    Does this content support the claim?
    Respond with ONLY a JSON object: {{"corroborates": "YES" or "NO", "weight": 0-100}}
    """
    response = gl.nondet.exec_prompt(prompt)

    try:
        data = json.loads(response)
    except:
        return {"url": url, "corroborates": False, "weight": 0}

    corroborates = str(data.get("corroborates", "NO")).upper() == "YES"
    try:
        weight = int(data.get("weight", 0))
    except:
        weight = 0
    weight = max(0, min(100, weight))

    return {"url": url, "corroborates": corroborates, "weight": weight}


def _compute_verdict(plaintiff_score: int, defendant_score: int) -> tuple:
    total = plaintiff_score + defendant_score
    if total == 0:
        return ("REFUND_TO_BUYER", 0)

    ratio = int((defendant_score / total) * 100)
    ratio = max(0, min(100, ratio))

    if ratio == 0:
        verdict = "REFUND_TO_BUYER"
    elif ratio == 100:
        verdict = "RELEASE_TO_SELLER"
    else:
        verdict = "SPLIT"

    return (verdict, ratio)


@allow_storage
@dataclass
class Dispute:
    dispute_id: u256
    escrow_id: u256
    plaintiff: str
    defendant: str
    claim: str
    status: str


@allow_storage
@dataclass
class Evidence:
    evidence_id: u256
    dispute_id: u256
    submitter: str
    side: str
    url: str
    domain: str
    description: str


@allow_storage
@dataclass
class Judgment:
    judgment_id: u256
    dispute_id: u256
    verdict: str
    compensation_ratio: u256
    plaintiff_evidence_count: u256
    defendant_evidence_count: u256
    reasoning_hash: str
    status: str


class DisputeResolver(gl.Contract):
    disputes: TreeMap[u256, Dispute]
    evidences: TreeMap[u256, Evidence]
    evidence_for_dispute: TreeMap[u256, str]
    evidence_domains_per_side: TreeMap[str, str]
    judgments: TreeMap[u256, Judgment]
    judgment_for_dispute: TreeMap[u256, u256]
    next_dispute_id: u256
    next_evidence_id: u256
    next_judgment_id: u256
    factory_contract: str
    deposit_contract: str

    def __init__(self, factory_address: str, deposit_address: str):
        self.next_dispute_id = u256(0)
        self.next_evidence_id = u256(0)
        self.next_judgment_id = u256(0)
        self.factory_contract = factory_address
        self.deposit_contract = deposit_address

    @gl.public.write
    def open_dispute(self, escrow_id: u256, claim: str) -> u256:
        assert claim.strip() != "", "Claim cannot be empty"

        caller = str(gl.message.sender_address)

        escrow_raw = gl.get_contract_at(
            Address(self.factory_contract)
        ).view().get_escrow_data(escrow_id)

        assert escrow_raw != "NOT_FOUND", "Escrow not found"

        try:
            escrow_data = json.loads(escrow_raw)
        except:
            raise gl.vm.UserError("Invalid escrow data from factory")

        buyer = escrow_data.get("buyer", "")
        seller = escrow_data.get("seller", "")
        status = escrow_data.get("status", "")

        assert status == "FUNDED", "Escrow must be FUNDED to open a dispute"
        assert caller == buyer or caller == seller, "Only buyer or seller can open a dispute"

        if caller == buyer:
            plaintiff = buyer
            defendant = seller
        else:
            plaintiff = seller
            defendant = buyer

        did = self.next_dispute_id
        self.next_dispute_id += u256(1)

        self.disputes[did] = Dispute(
            dispute_id=did,
            escrow_id=escrow_id,
            plaintiff=plaintiff,
            defendant=defendant,
            claim=claim,
            status="EVIDENCE_PHASE",
        )
        self.evidence_for_dispute[did] = ""

        gl.get_contract_at(
            Address(self.factory_contract)
        ).emit().mark_disputed(escrow_id, str(gl.message.sender_address))

        return did

    @gl.public.write
    def submit_evidence(self, dispute_id: u256, url: str, description: str) -> u256:
        assert dispute_id in self.disputes, "Dispute not found"
        dispute = self.disputes[dispute_id]
        assert dispute.status == "EVIDENCE_PHASE", "Dispute is not in evidence phase"
        assert url.strip() != "", "URL cannot be empty"

        caller = str(gl.message.sender_address)
        if caller == dispute.plaintiff:
            side = "PLAINTIFF"
        elif caller == dispute.defendant:
            side = "DEFENDANT"
        else:
            raise gl.vm.UserError("Only plaintiff or defendant can submit evidence")

        normalized = _normalize_url(url)
        assert normalized != "", "Invalid URL"

        domain = _extract_domain(normalized)
        assert domain != "", "Could not extract domain"

        # Check URL uniqueness within dispute
        existing = self.evidence_for_dispute[dispute_id]
        existing_ids = [x for x in existing.split(',') if x]
        for eid_str in existing_ids:
            eid_int = int(eid_str)
            ev = self.evidences[u256(eid_int)]
            assert ev.url != normalized, "Duplicate source URL in this dispute"

        # Check domain uniqueness per side
        side_key = f"{dispute_id}:{side}"
        existing_domains = self.evidence_domains_per_side.get(side_key, "")
        domain_list = [d for d in existing_domains.split(',') if d]
        assert domain not in domain_list, "Duplicate domain for this side"

        eid = self.next_evidence_id
        self.next_evidence_id += u256(1)

        self.evidences[eid] = Evidence(
            evidence_id=eid,
            dispute_id=dispute_id,
            submitter=caller,
            side=side,
            url=normalized,
            domain=domain,
            description=description,
        )

        # Append to dispute evidence list
        if existing == "":
            self.evidence_for_dispute[dispute_id] = str(int(eid))
        else:
            self.evidence_for_dispute[dispute_id] = existing + "," + str(int(eid))

        # Append domain to side list
        if existing_domains == "":
            self.evidence_domains_per_side[side_key] = domain
        else:
            self.evidence_domains_per_side[side_key] = existing_domains + "," + domain

        return eid

    @gl.public.write
    def resolve(self, dispute_id: u256) -> u256:
        assert dispute_id in self.disputes, "Dispute not found"
        dispute = self.disputes[dispute_id]
        assert dispute.status == "EVIDENCE_PHASE", "Dispute is not in evidence phase"
        assert dispute_id not in self.judgment_for_dispute, "Dispute already resolved"

        # Collect evidence list
        evidence_ids_str = self.evidence_for_dispute[dispute_id]
        evidence_ids = [int(x) for x in evidence_ids_str.split(',') if x]
        assert len(evidence_ids) >= 1, "At least one evidence is required"

        plaintiff_urls = []
        defendant_urls = []
        for eid_int in evidence_ids:
            ev = self.evidences[u256(eid_int)]
            if ev.side == "PLAINTIFF":
                plaintiff_urls.append(ev.url)
            else:
                defendant_urls.append(ev.url)

        claim = dispute.claim

        def leader_fn():
            plaintiff_score = 0
            defendant_score = 0

            for url in plaintiff_urls:
                r = _fetch_and_evaluate(url, claim, "PLAINTIFF")
                if r["corroborates"]:
                    plaintiff_score += r["weight"]

            for url in defendant_urls:
                r = _fetch_and_evaluate(url, claim, "DEFENDANT")
                if r["corroborates"]:
                    defendant_score += r["weight"]

            verdict, ratio = _compute_verdict(plaintiff_score, defendant_score)

            reasoning_text = f"plaintiff_score={plaintiff_score};defendant_score={defendant_score};verdict={verdict};ratio={ratio}"
            reasoning_hash = hashlib.sha256(reasoning_text.encode()).hexdigest()[:16]

            return {
                "verdict": verdict,
                "compensation_ratio": ratio,
                "plaintiff_evidence_count": len(plaintiff_urls),
                "defendant_evidence_count": len(defendant_urls),
                "plaintiff_score": plaintiff_score,
                "defendant_score": defendant_score,
                "reasoning_hash": reasoning_hash,
            }

        def validator_fn(leader_result):
            if not isinstance(leader_result, gl.vm.Return):
                return False

            leader_data = leader_result.calldata

            leader_verdict = leader_data.get("verdict")
            leader_ratio = leader_data.get("compensation_ratio")
            leader_p_count = leader_data.get("plaintiff_evidence_count")
            leader_d_count = leader_data.get("defendant_evidence_count")

            if leader_verdict not in ("REFUND_TO_BUYER", "RELEASE_TO_SELLER", "SPLIT"):
                return False
            if not isinstance(leader_ratio, int) or not (0 <= leader_ratio <= 100):
                return False
            if leader_p_count != len(plaintiff_urls):
                return False
            if leader_d_count != len(defendant_urls):
                return False

            # Independent recomputation
            validator_plaintiff_score = 0
            validator_defendant_score = 0

            for url in plaintiff_urls:
                r = _fetch_and_evaluate(url, claim, "PLAINTIFF")
                if r["corroborates"]:
                    validator_plaintiff_score += r["weight"]

            for url in defendant_urls:
                r = _fetch_and_evaluate(url, claim, "DEFENDANT")
                if r["corroborates"]:
                    validator_defendant_score += r["weight"]

            validator_verdict, validator_ratio = _compute_verdict(
                validator_plaintiff_score, validator_defendant_score
            )

            if validator_verdict != leader_verdict:
                return False
            if validator_ratio != leader_ratio:
                return False

            # Invariant check: verdict must match ratio
            expected_verdict, _ = _compute_verdict(leader_ratio, 100 - leader_ratio)
            # Recompute expected from ratio directly
            if leader_ratio == 0:
                expected_from_ratio = "REFUND_TO_BUYER"
            elif leader_ratio == 100:
                expected_from_ratio = "RELEASE_TO_SELLER"
            else:
                expected_from_ratio = "SPLIT"
            if expected_from_ratio != leader_verdict:
                return False

            return True

        result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

        jid = self.next_judgment_id
        self.next_judgment_id += u256(1)

        self.judgments[jid] = Judgment(
            judgment_id=jid,
            dispute_id=dispute_id,
            verdict=result["verdict"],
            compensation_ratio=u256(result["compensation_ratio"]),
            plaintiff_evidence_count=u256(result["plaintiff_evidence_count"]),
            defendant_evidence_count=u256(result["defendant_evidence_count"]),
            reasoning_hash=result["reasoning_hash"],
            status="FINAL",
        )
        self.judgment_for_dispute[dispute_id] = jid

        dispute.status = "RESOLVED"
        self.disputes[dispute_id] = dispute

        return jid

    @gl.public.view
    def get_dispute_status(self, dispute_id: u256) -> str:
        if dispute_id not in self.disputes:
            return "NOT_FOUND"
        return self.disputes[dispute_id].status

    @gl.public.view
    def get_dispute_details(self, dispute_id: u256) -> str:
        if dispute_id not in self.disputes:
            return "NOT_FOUND"
        d = self.disputes[dispute_id]
        return json.dumps({
            "dispute_id": int(d.dispute_id),
            "escrow_id": int(d.escrow_id),
            "plaintiff": d.plaintiff,
            "defendant": d.defendant,
            "claim": d.claim,
            "status": d.status,
        })

    @gl.public.view
    def get_dispute_data(self, dispute_id: u256) -> str:
        if dispute_id not in self.disputes:
            return "NOT_FOUND"
        d = self.disputes[dispute_id]
        return json.dumps({
            "dispute_id": int(d.dispute_id),
            "escrow_id": int(d.escrow_id),
            "plaintiff": d.plaintiff,
            "defendant": d.defendant,
            "status": d.status,
        })

    @gl.public.view
    def get_judgment(self, dispute_id: u256) -> str:
        if dispute_id not in self.judgment_for_dispute:
            return "NOT_FOUND"
        jid = self.judgment_for_dispute[dispute_id]
        j = self.judgments[jid]
        return f"{j.verdict}:{int(j.compensation_ratio)}"

    @gl.public.view
    def get_judgment_data(self, judgment_id: u256) -> str:
        if judgment_id not in self.judgments:
            return "NOT_FOUND"
        j = self.judgments[judgment_id]
        return json.dumps({
            "judgment_id": int(j.judgment_id),
            "dispute_id": int(j.dispute_id),
            "verdict": j.verdict,
            "compensation_ratio": int(j.compensation_ratio),
            "plaintiff_evidence_count": int(j.plaintiff_evidence_count),
            "defendant_evidence_count": int(j.defendant_evidence_count),
            "reasoning_hash": j.reasoning_hash,
            "status": j.status,
        })

    @gl.public.view
    def get_judgment_details(self, judgment_id: u256) -> str:
        if judgment_id not in self.judgments:
            return "NOT_FOUND"
        j = self.judgments[judgment_id]
        return json.dumps({
            "judgment_id": int(j.judgment_id),
            "dispute_id": int(j.dispute_id),
            "verdict": j.verdict,
            "compensation_ratio": int(j.compensation_ratio),
            "reasoning_hash": j.reasoning_hash,
            "status": j.status,
        })

    @gl.public.view
    def list_disputes_for_escrow(self, escrow_id: u256) -> str:
        items = []
        for key in self.disputes:
            d = self.disputes[key]
            if d.escrow_id == escrow_id:
                items.append(str(int(d.dispute_id)))
        return ",".join(items)

    @gl.public.view
    def list_evidence_for_dispute(self, dispute_id: u256) -> str:
        return self.evidence_for_dispute.get(dispute_id, "")

    @gl.public.view
    def get_evidence_details(self, evidence_id: u256) -> str:
        if evidence_id not in self.evidences:
            return "NOT_FOUND"
        ev = self.evidences[evidence_id]
        return json.dumps({
            "evidence_id": int(ev.evidence_id),
            "dispute_id": int(ev.dispute_id),
            "submitter": ev.submitter,
            "side": ev.side,
            "url": ev.url,
            "domain": ev.domain,
            "description": ev.description,
        })

    @gl.public.view
    def list_all_disputes(self) -> str:
        items = []
        for key in self.disputes:
            d = self.disputes[key]
            items.append(f"{int(d.dispute_id)}:{d.status}")
        return ",".join(items)
