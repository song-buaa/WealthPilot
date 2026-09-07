from backend.services.consumption.presentation import account_display_label


def test_known_consumption_accounts_use_localized_labels_and_safe_masks():
    assert account_display_label("CCB Credit ****1234", "CCB", "CREDIT_CARD") == "建行信用卡 ****"
    assert account_display_label("CMB Credit ****4964", "CMB", "CREDIT_CARD") == "招行信用卡 ****"
    assert account_display_label("CMB Debit ****6789", "CMB", "DEBIT_CARD") == "招行借记卡 ****"
    assert account_display_label(None, "CMB", "DEBIT_CARD", include_mask=False) == "招行借记卡"
