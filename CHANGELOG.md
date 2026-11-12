# Changelog

## V5.1 - 12/11/2026

- Sửa Claude Code luôn hiện "Dữ liệu cũ": endpoint `oauth/usage` bị gọi mỗi 30 giây nên liên tục trả HTTP 429 và app không bao giờ ngừng gọi.
- Claude Code có chu kỳ riêng, tối thiểu 3 phút/lần; các dịch vụ khác giữ chu kỳ chung.
- Tự lùi (backoff) khi gặp HTTP 429: 5 → 10 → 20 → 30 phút, dùng `Retry-After` nếu có; hồi phục ngay khi gọi thành công. Làm mới thủ công không bỏ qua thời gian tạm dừng.
- Thẻ **CŨ** hiện tuổi dữ liệu, lý do lỗi thật và thời gian thử lại, tự co gọn theo bề rộng cửa sổ, tự cập nhật mỗi phút.

## V5.0 - 12/11/2026

- Thay ChatGPT favicon (sai biểu tượng OpenAI) bằng asset OpenAI Blossom trắng/đen đóng gói sẵn; không dùng cache `codex.png` cũ.
- Sửa Claude Code hiển thị trùng `five_hour`/`session`, `seven_day`/`weekly` từ API `/limits`; chuẩn hóa cả cache V4.
- Bổ sung thay đổi kích thước cửa sổ bằng chuột: kéo 4 cạnh và 4 góc, grip góc dưới phải.
- Lưu chiều rộng/chiều cao; nhấp đúp góc dưới phải đặt lại tự động; thay đổi độ nén layout theo chiều cao người dùng chọn.
- Mở rộng biểu đồ khi cửa sổ cao, giữ nguyên no-scroll và logic giới hạn kích thước để không cắt mất hạn mức.
- Giữ nguyên Light/Dark, opacity, refresh, autostart, lọc dịch vụ, lịch sử token.

## V4.0 - 12/11/2026

- Chiều cao tự động theo số dịch vụ và quota; biểu đồ token ở cuối, bỏ cuộn.
- Tự đổi density theo chiều cao màn hình.

## V3.0 - 12/11/2026

- Ẩn/hiện từng dịch vụ, mặc định Codex + Claude Code; biểu đồ theo khoảng ngày.
