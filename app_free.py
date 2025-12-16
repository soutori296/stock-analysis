return {
            "code": ticker, "name": info["name"], "price": curr_price, "cap_val": info["cap"], "cap_disp": fmt_market_cap(info["cap"]), "per": info["per"], "pbr": info["pbr"],
            "rsi": rsi_val, "rsi_disp": f"{rsi_mark}{rsi_val:.1f}", "vol_ratio": vol_ratio, "vol_disp": vol_disp, "momentum": momentum_str, "strategy": strategy, "score": score_to_return,
            "buy": buy_target, "p_half": p_half, "p_full": p_full, "backtest": bt_str, "backtest_raw": bt_raw, "max_dd_pct": max_dd_pct, "sl_pct": sl_pct, "sl_ma": sl_ma,
            "avg_volume_5d": avg_vol_5d, "is_low_liquidity": avg_vol_5d < 1000, "risk_reward": risk_reward_ratio, "risk_value": risk_value, "issued_shares": issued_shares, "liquidity_ratio_pct": liquidity_ratio_pct,
            "atr_val": atr_val, "atr_smoothed": atr_smoothed, "is_gc": is_gc, "is_dc": is_dc, "ma25": ma25, "atr_sl_price": atr_sl_price, "score_diff": score_diff,
            "base_score": base_score, "is_aoteng": is_aoteng, "run_count": current_run_count, "win_rate_pct": win_rate_pct, "bt_trade_count": bt_cnt, "bt_win_count": bt_win_count,
            "bt_loss_count": bt_loss_count, 
            "bt_target_pct": bt_target_pct, # 💡【追加】この行を追加
            "score_factors": japanese_score_factors, 
            "atr_pct": atr_pct_val, "atr_comment": atr_comment, 
        }
