"""分群用户分段交付测试：用四条稳定编号记录复现确认、中断、更正、恢复与复核。"""
from __future__ import annotations

import csv
import io

import pytest

from app.services.data_extract_delivery import (
    DataExtractDeliveryService,
    DeliveryError,
    parse_filter,
)
from app.services.pipeline_orchestrator import publish_delivery_report_to_report_gen
from app.store import store

TASK_ID = 1  # DATA-HV-USER，来源 cohort_users，ltv>=1000 and region=华东


def csv_rows(content: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(content.lstrip("﻿"))))


@pytest.fixture()
def svc():
    return DataExtractDeliveryService()


def start(svc: DataExtractDeliveryService) -> dict:
    return svc.start_delivery(TASK_ID, {"分段大小": 2})


class TestStartDelivery:
    def test_snapshot_uses_threshold_and_region(self, svc):
        delivery = start(svc)
        assert delivery["filter"] == "ltv>=1000 and region=华东"
        assert delivery["total_members"] == 4
        assert len(delivery["segments"]) == 2

    def test_segments_cover_snapshot_without_overlap(self, svc):
        delivery = start(svc)
        offsets = [(s["start_offset"], s["end_offset"]) for s in delivery["segments"]]
        assert offsets == [(1, 2), (3, 4)]
        assert sum(s["member_count"] for s in delivery["segments"]) == delivery["total_members"]
        users = [row["user_id"] for row in svc._snapshot(
            parse_filter(delivery["filter"]), [("ltv", True)])[0]]
        assert users == ["U20003", "U20001", "U20002", "U20004"]

    def test_business_cutoff_recorded(self, svc):
        delivery = start(svc)
        assert delivery["business_cutoff_at"]
        assert delivery["version"] == "v1"

    def test_filter_rejects_unknown_syntax(self, svc):
        with pytest.raises(DeliveryError):
            svc.start_delivery(TASK_ID, {"筛选条件": "ltv >= "})

    def test_no_match_is_rejected(self, svc):
        with pytest.raises(DeliveryError):
            svc.start_delivery(TASK_ID, {"筛选条件": "region=火星"})


class TestInterruptAndCorrect:
    """确认第一段后中断第二段，同时更正一位成员的消费额使其跨过门槛。"""

    def test_interrupt_keeps_confirmed_scope(self, svc):
        delivery = start(svc)
        delivery, message = svc.run_action(TASK_ID, delivery["id"], "确认分段", 1)
        assert delivery["segments"][0]["status"] == "已确认"
        delivery, message = svc.run_action(TASK_ID, delivery["id"], "中断交付", 2)
        assert delivery["status"] == "已中断"
        assert delivery["segments"][1]["status"] == "未送达"
        assert delivery["confirmed_segment_count"] == 1
        assert delivery["confirmed_member_count"] == 2
        assert delivery["undelivered_member_count"] == 2

    def test_correction_raises_ltv_above_threshold(self, svc):
        start(svc)
        before = next(row for row in store.rows("cohort_users") if row["user_id"] == "U20005")
        assert before["ltv"] < 1000
        svc.update_source_user("U20005", {"ltv": 1600})
        after = next(row for row in store.rows("cohort_users") if row["user_id"] == "U20005")
        assert after["ltv"] == 1600.0

    def test_download_only_confirmed_members(self, svc):
        delivery = start(svc)
        svc.run_action(TASK_ID, delivery["id"], "确认分段", 1)
        svc.run_action(TASK_ID, delivery["id"], "中断交付", 2)
        content, filename, headers = svc.build_csv(TASK_ID, delivery["id"])
        rows = csv_rows(content)
        assert [row["user_id"] for row in rows] == ["U20003", "U20001"]
        assert filename.endswith("-partial.csv")
        assert headers["X-Delivery-Status"] == "interrupted"
        assert headers["X-Member-Count"] == "2"

    def test_undelivered_segment_cannot_be_downloaded(self, svc):
        delivery = start(svc)
        svc.run_action(TASK_ID, delivery["id"], "确认分段", 1)
        svc.run_action(TASK_ID, delivery["id"], "中断交付", 2)
        with pytest.raises(DeliveryError):
            svc.build_csv(TASK_ID, delivery["id"], 2)

    def test_report_excludes_undelivered_and_uses_snapshot(self, svc):
        delivery = start(svc)
        svc.run_action(TASK_ID, delivery["id"], "确认分段", 1)
        svc.run_action(TASK_ID, delivery["id"], "中断交付", 2)
        svc.update_source_user("U20005", {"ltv": 1600})
        report = svc.report(TASK_ID, delivery["id"])
        assert report["member_count"] == 2
        assert report["total_ltv"] == 4100.0
        assert report["confirmed_segments"] == [1]
        assert report["undelivered_segments"] == [2]
        assert report["includes_undelivered"] is False
        # 当前来源已多出 U20005，但报表不并入
        assert report["live_reference"]["matched_count"] == 5
        assert "U20005" in report["drift"]["added_user_ids"]


class TestResumeAndReconcile:
    def _run_scenario(self, svc):
        delivery = start(svc)
        svc.run_action(TASK_ID, delivery["id"], "确认分段", 1)
        svc.run_action(TASK_ID, delivery["id"], "中断交付", 2)
        svc.update_source_user("U20005", {"ltv": 1600})
        return delivery

    def test_resume_reuses_original_cutoff(self, svc):
        delivery = self._run_scenario(svc)
        cutoff = delivery["business_cutoff_at"]
        resumed, message = svc.run_action(TASK_ID, delivery["id"], "恢复交付")
        assert resumed["status"] == "交付中"
        assert resumed["segments"][1]["status"] == "待确认"
        assert resumed["business_cutoff_at"] == cutoff

    def test_file_has_unique_users_and_report_matches(self, svc):
        delivery = self._run_scenario(svc)
        svc.run_action(TASK_ID, delivery["id"], "恢复交付")
        delivery, _ = svc.run_action(TASK_ID, delivery["id"], "确认分段", 2)
        assert delivery["status"] == "已完成"

        content, filename, headers = svc.build_csv(TASK_ID, delivery["id"])
        rows = csv_rows(content)
        user_ids = [row["user_id"] for row in rows]
        assert len(user_ids) == len(set(user_ids)) == 4
        assert set(user_ids) == {"U20001", "U20002", "U20003", "U20004"}
        assert filename.endswith("-confirmed.csv")
        assert headers["X-Delivery-Status"] == "completed"
        assert headers["X-Delivery-Version"] == "v1"
        assert headers["X-Business-Cutoff"] == delivery["business_cutoff_at"]

        report = svc.report(TASK_ID, delivery["id"])
        assert report["member_count"] == len(rows)
        assert report["unique_user_count"] == len(rows)
        assert report["total_ltv"] == round(sum(float(row["ltv"]) for row in rows), 2)
        assert all(float(row["ltv"]) >= 1000 for row in rows)
        assert all(row["region"] == "华东" for row in rows)
        assert "U20005" not in user_ids
        assert report["drift"]["added_user_ids"] == ["U20005"]
        assert report["threshold"]["conditions"][0] == {"field": "ltv", "op": ">=", "value": 1000}
        # 报表与文件可直接核对：行数、唯一用户、校验码
        file_check = report["file_check"]
        assert file_check["last_confirmed_download"]["row_count"] == len(rows)
        assert file_check["last_confirmed_download"]["unique_user_count"] == len(rows)
        assert file_check["expected_row_count"] == len(rows)
        assert file_check["last_confirmed_download"]["checksum"] == file_check["expected_checksum"]

    def test_partial_download_not_merged_with_resumed_half(self, svc):
        """中断时下载过部分结果，恢复并确认后再次下载：只保留同一交付版本的口径。"""
        delivery = self._run_scenario(svc)
        partial, partial_name, partial_headers = svc.build_csv(TASK_ID, delivery["id"])
        assert partial_name.endswith("-partial.csv")
        assert partial_headers["X-Delivery-Status"] == "interrupted"
        assert len(csv_rows(partial)) == 2

        svc.run_action(TASK_ID, delivery["id"], "恢复交付")
        delivery, _ = svc.run_action(TASK_ID, delivery["id"], "确认分段", 2)
        full, full_name, full_headers = svc.build_csv(TASK_ID, delivery["id"])
        assert full_name.endswith("-confirmed.csv")
        assert full_headers["X-Delivery-Status"] == "completed"
        rows = csv_rows(full)
        # 不是把两个半份结果拼接：仍是 4 人，且编号唯一
        assert len(rows) == 4
        assert len({row["user_id"] for row in rows}) == 4
        assert partial_headers["X-Business-Cutoff"] == full_headers["X-Business-Cutoff"]
        report = svc.report(TASK_ID, delivery["id"])
        assert report["member_count"] == 4
        assert report["file_check"]["last_confirmed_download"]["checksum"] == report["file_check"]["expected_checksum"]

    def test_new_delivery_version_picks_up_correction(self, svc):
        self._run_scenario(svc)
        second = start(svc)
        assert second["version"] == "v2"
        assert second["total_members"] == 5
        assert second["business_cutoff_at"] >= second["created_at"]
        users = [row["user_id"] for row in svc._snapshot(
            parse_filter(second["filter"]), [("ltv", True)])[0]]
        assert "U20005" in users

    def test_task_status_follows_delivery(self, svc):
        delivery = start(svc)
        svc.run_action(TASK_ID, delivery["id"], "确认分段", 1)
        svc.run_action(TASK_ID, delivery["id"], "中断交付", 2)
        assert store.find("data_extract", TASK_ID)["最近交付状态"] == "已中断"
        svc.run_action(TASK_ID, delivery["id"], "恢复交付")
        svc.run_action(TASK_ID, delivery["id"], "确认分段", 2)
        task = store.find("data_extract", TASK_ID)
        assert task["status"] == "已完成"
        assert task["交付版本"] == "v1"

    def test_report_reused_in_cohort_report(self, svc):
        delivery = self._run_scenario(svc)
        svc.run_action(TASK_ID, delivery["id"], "恢复交付")
        svc.run_action(TASK_ID, delivery["id"], "确认分段", 2)
        outcome = publish_delivery_report_to_report_gen(TASK_ID, delivery["id"])
        assert outcome["ok"] is True
        entry = outcome["entry"]
        assert entry["报表类型"] == "人群报表"
        assert "v1" in entry["数据范围"]
        assert entry["交付报表"]["member_count"] == 4
        assert entry["交付报表"]["total_ltv"] == 6300.0


class TestGuardRails:
    def test_duplicate_source_user_does_not_duplicate_file_rows(self, svc):
        """来源里重复的 user_id 在快照与下载文件中只保留一条，避免人数虚高。"""
        store.rows("cohort_users").append({
            "user_id": "U20001", "region": "华东", "channel": "app",
            "ltv": 1500.0, "tag": "高价值", "last_order_date": "2026-08-28",
        })
        delivery = start(svc)
        assert delivery["duplicates_removed"] == 1
        assert delivery["total_members"] == 4
        svc.run_action(TASK_ID, delivery["id"], "确认分段", 1)
        svc.run_action(TASK_ID, delivery["id"], "确认分段", 2)
        content, _, _ = svc.build_csv(TASK_ID, delivery["id"])
        user_ids = [row["user_id"] for row in csv_rows(content)]
        assert len(user_ids) == len(set(user_ids)) == 4
        report = svc.report(TASK_ID, delivery["id"])
        assert report["member_count"] == report["unique_user_count"] == 4

    def test_cannot_confirm_after_interrupt(self, svc):
        delivery = start(svc)
        svc.run_action(TASK_ID, delivery["id"], "中断交付")
        with pytest.raises(DeliveryError):
            svc.run_action(TASK_ID, delivery["id"], "确认分段", 1)

    def test_cannot_interrupt_confirmed_segment(self, svc):
        delivery = start(svc)
        svc.run_action(TASK_ID, delivery["id"], "确认分段", 1)
        with pytest.raises(DeliveryError):
            svc.run_action(TASK_ID, delivery["id"], "中断交付", 1)

    def test_unknown_segment_rejected(self, svc):
        delivery = start(svc)
        with pytest.raises(DeliveryError):
            svc.run_action(TASK_ID, delivery["id"], "确认分段", 99)

    def test_negative_ltv_rejected(self, svc):
        with pytest.raises(DeliveryError):
            svc.update_source_user("U20001", {"ltv": -1})

    def test_unknown_source_field_rejected(self, svc):
        with pytest.raises(DeliveryError):
            svc.update_source_user("U20001", {"user_id": "U99999"})

    def test_non_cohort_task_rejected(self, svc):
        """order_events 之类的来源不是分群用户数据集，不能走分段交付。"""
        with pytest.raises(DeliveryError) as error:
            svc.start_delivery(2)
        assert "cohort_users" in str(error.value)

    def test_unknown_action_rejected(self, svc):
        delivery = start(svc)
        with pytest.raises(DeliveryError):
            svc.run_action(TASK_ID, delivery["id"], "删除交付")
