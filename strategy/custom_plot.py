from func_price_klines import get_price_klines
from func_plot_trends import plot_trends

# 1. Điền 2 đồng coin mà bạn muốn kiểm tra vào đây (nhớ thêm hậu tố USDT)
coin_1 = ""
coin_2 = ""

def main():
    print(f"Đang tải dữ liệu giá klines cho {coin_1} và {coin_2}...")
    
    # Lấy dữ liệu lịch sử giá của 2 coin
    price_data_coin_1 = get_price_klines(coin_1)
    price_data_coin_2 = get_price_klines(coin_2)
    
    # Kiểm tra xem dữ liệu trả về có hợp lệ không
    if len(price_data_coin_1) > 0 and len(price_data_coin_2) > 0:
        
        # Tạo dictionary dữ liệu giá theo đúng định dạng mà hàm plot_trends yêu cầu
        price_data = {
            coin_1: price_data_coin_1,
            coin_2: price_data_coin_2
        }
        
        print(f"Đã tải xong dữ liệu. Đang vẽ biểu đồ cho {coin_1} vs {coin_2}...")
        
        # Gọi hàm vẽ biểu đồ và xuất file CSV backtest
        plot_trends(coin_1, coin_2, price_data)
        
    else:
        print("Lỗi: Không lấy được dữ liệu cho một trong hai đồng coin. Vui lòng kiểm tra lại tên cặp giao dịch.")

if __name__ == "__main__":
    main()