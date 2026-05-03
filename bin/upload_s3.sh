#!/usr/bin/env bash
#
# R006 · bin/upload_s3.sh — Vendor S3 批量上传 (multipart, 断点续传)
#
# 用法:
#   bash bin/upload_s3.sh \
#     --batch-dir ./out/batch-2026-05-A/ \
#     --bucket oysterworld-gamedata-vendor-uploads \
#     --vendor-id vendor-001 \
#     --region us-east-1 \
#     [--dry-run] \
#     [--profile default]
#

set -euo pipefail

# ============================================================================
# 全局变量
# ============================================================================
SCRIPT_NAME="$(basename "$0")"
BATCH_DIR=""
BUCKET=""
VENDOR_ID=""
REGION=""
DRY_RUN=false
PROFILE_OPTION=""
START_TIME=""
END_TIME=""
UPLOAD_SUCCESS=0
UPLOAD_FAILED=0
TOTAL_FILES=0
TOTAL_SIZE_BYTES=0

# ============================================================================
# 辅助函数
# ============================================================================

log_step() {
    local step_num="$1"
    local message="$2"
    local status="${3:-}"
    printf "[STEP %d/7] %s" "$step_num" "$message"
    if [[ -n "$status" ]]; then
        printf " %s" "$status"
    fi
    printf "\n"
}

log_success() {
    printf " ✅ %s\n" "$1"
}

log_error() {
    printf " ❌ %s\n" "$1" >&2
}

usage() {
    cat <<EOF
用法: $SCRIPT_NAME --batch-dir DIR --bucket BUCKET --vendor-id ID --region REGION [OPTIONS]

必需参数:
  --batch-dir DIR      包含 manifest.yaml 和 tarball 的目录
  --bucket BUCKET      S3 桶名称
  --vendor-id ID       供应商 ID
  --region REGION      AWS 区域

可选参数:
  --dry-run            只打印计划,不实际上传
  --profile PROFILE    AWS CLI profile 名称
  -h, --help           显示此帮助信息

退出码:
  0  全部成功
  1  部分失败
  2  配置错误
  3  验证失败
EOF
}

# 计算 estimated upload 时间 (假设 50 Mbps 上行)
# 参数: $1 = 字节数
# 输出: 格式化的时间字符串
estimate_duration() {
    local bytes="$1"
    local mbps=50
    local bits_per_byte=8
    local total_bits=$((bytes * bits_per_byte))
    local bits_per_second=$((mbps * 1000000))
    
    if [[ "$bits_per_second" -eq 0 ]]; then
        echo "0s"
        return
    fi
    
    local total_seconds=$((total_bits / bits_per_second))
    local hours=$((total_seconds / 3600))
    local minutes=$(((total_seconds % 3600) / 60))
    local seconds=$((total_seconds % 60))
    
    if [[ "$hours" -gt 0 ]]; then
        echo "${hours}h ${minutes}m"
    elif [[ "$minutes" -gt 0 ]]; then
        echo "${minutes}m ${seconds}s"
    else
        echo "${seconds}s"
    fi
}

# 格式化字节大小为人类可读格式
format_size() {
    local bytes="$1"
    local units=("B" "KB" "MB" "GB" "TB")
    local unit=0
    local size="$bytes"
    
    while (( $(echo "$size >= 1024 && $unit < 4" | bc -l) )); do
        size=$(echo "scale=2; $size / 1024" | bc)
        ((unit++))
    done
    
    printf "%.1f %s" "$size" "${units[$unit]}"
}

# 验证上传: 比对本地和远程文件清单
verify_upload() {
    local local_dir="$1"
    local bucket="$2"
    local s3_prefix="$3"
    local profile_opt="$4"
    
    local local_files=()
    local remote_files=()
    local match_count=0
    local mismatch_count=0
    
    # 获取本地文件列表 (相对路径和大小)
    while IFS= read -r -d '' file; do
        local rel_path="${file#$local_dir/}"
        local size
        size=$(stat -f%z "$file" 2>/dev/null || stat -c%s "$file" 2>/dev/null)
        local_files+=("$rel_path:$size")
    done < <(find "$local_dir" -type f -print0)
    
    # 获取远程文件列表
    local s3_list
    s3_list=$(aws s3 ls "s3://${bucket}/${s3_prefix}" --recursive $profile_opt --region "$REGION" 2>/dev/null) || {
        log_error "无法列出 S3 桶内容"
        return 1
    }
    
    # 解析远程文件列表
    while IFS= read -r line; do
        if [[ -n "$line" ]]; then
            local size
            size=$(echo "$line" | awk '{print $3}')
            local path
            path=$(echo "$line" | awk '{print $4}')
            # 移除前缀路径
            local rel_path="${path#${s3_prefix}}"
            rel_path="${rel_path#/}"
            remote_files+=("$rel_path:$size")
        fi
    done <<< "$s3_list"
    
    # 比对文件
    for local_entry in "${local_files[@]}"; do
        local found=false
        local local_name="${local_entry%:*}"
        local local_size="${local_entry##*:}"
        
        for remote_entry in "${remote_files[@]}"; do
            local remote_name="${remote_entry%:*}"
            local remote_size="${remote_entry##*:}"
            
            if [[ "$local_name" == "$remote_name" && "$local_size" == "$remote_size" ]]; then
                found=true
                ((match_count++))
                break
            fi
        done
        
        if [[ "$found" == false ]]; then
            ((mismatch_count++))
            echo "  Mismatch: $local_name" >&2
        fi
    done
    
    echo "$match_count:${#local_files[@]}"
    
    if [[ "$mismatch_count" -gt 0 ]]; then
        return 1
    fi
    return 0
}

# ============================================================================
# 清理和摘要输出 (trap handler)
# ============================================================================
cleanup_and_summary() {
    local exit_code=$?
    
    if [[ -n "$START_TIME" ]]; then
        END_TIME=$(date +%s)
        local duration=$((END_TIME - START_TIME))
        local minutes=$((duration / 60))
        local seconds=$((duration % 60))
        
        echo ""
        echo "=========================================="
        echo "上传摘要"
        echo "=========================================="
        echo "总文件数: $TOTAL_FILES"
        echo "成功: $UPLOAD_SUCCESS"
        echo "失败: $UPLOAD_FAILED"
        echo "总大小: $(format_size "$TOTAL_SIZE_BYTES")"
        echo "总耗时: ${minutes}m ${seconds}s"
        
        # 估算费用 (假设 $0.02/GB 跨区域传输)
        local size_gb
        size_gb=$(echo "scale=4; $TOTAL_SIZE_BYTES / 1073741824" | bc)
        local cost
        cost=$(echo "scale=2; $size_gb * 0.02" | bc)
        echo "预估费用: ~\$${cost} transfer"
        echo "=========================================="
    fi
    
    exit $exit_code
}

# ============================================================================
# 参数解析
# ============================================================================
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --batch-dir)
                BATCH_DIR="$2"
                shift 2
                ;;
            --bucket)
                BUCKET="$2"
                shift 2
                ;;
            --vendor-id)
                VENDOR_ID="$2"
                shift 2
                ;;
            --region)
                REGION="$2"
                shift 2
                ;;
            --dry-run)
                DRY_RUN=true
                shift
                ;;
            --profile)
                PROFILE_OPTION="--profile $2"
                shift 2
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                log_error "未知参数: $1"
                usage
                exit 2
                ;;
        esac
    done
    
    # 验证必需参数
    if [[ -z "$BATCH_DIR" || -z "$BUCKET" || -z "$VENDOR_ID" || -z "$REGION" ]]; then
        log_error "缺少必需参数"
        usage
        exit 2
    fi
}

# ============================================================================
# 主流程
# ============================================================================
main() {
    trap cleanup_and_summary EXIT
    START_TIME=$(date +%s)
    
    # STEP 1: 检查 awscli
    log_step 1 "Checking awscli..."
    if ! command -v aws &>/dev/null; then
        log_error "awscli 未安装"
        echo "请安装 awscli: pip install awscli 或 brew install awscli" >&2
        exit 2
    fi
    
    local aws_version
    aws_version=$(aws --version 2>&1 | head -1)
    log_success "$aws_version"
    
    # STEP 2: 检查 AWS credentials
    log_step 2 "Checking AWS credentials..."
    local caller_identity
    if ! caller_identity=$(aws sts get-caller-identity $PROFILE_OPTION --region "$REGION" 2>&1); then
        log_error "AWS credentials 无效或未配置"
        echo "$caller_identity" >&2
        exit 2
    fi
    
    local caller_arn
    caller_arn=$(echo "$caller_identity" | grep -o '"Arn": "[^"]*"' | cut -d'"' -f4)
    log_success "$caller_arn"
    
    # STEP 3: 验证 batch 目录
    log_step 3 "Validating batch dir..."
    
    if [[ ! -d "$BATCH_DIR" ]]; then
        log_error "batch-dir 不存在: $BATCH_DIR"
        exit 2
    fi
    
    if [[ ! -f "$BATCH_DIR/manifest.yaml" ]]; then
        log_error "manifest.yaml 不存在于 $BATCH_DIR"
        exit 2
    fi
    
    # 统计 tarball 文件
    local tarball_count
    tarball_count=$(find "$BATCH_DIR" -maxdepth 1 -type f \( -name "*.tar.gz" -o -name "*.tgz" -o -name "*.tar" \) | wc -l | tr -d ' ')
    
    if [[ "$tarball_count" -lt 1 ]]; then
        log_error "batch-dir 中未找到 tarball 文件 (*.tar.gz, *.tgz, *.tar)"
        exit 2
    fi
    
    # 计算总大小
    TOTAL_SIZE_BYTES=$(find "$BATCH_DIR" -type f -exec stat -f%z {} \; 2>/dev/null | awk '{sum+=$1} END {print sum}' || \
                       find "$BATCH_DIR" -type f -exec stat -c%s {} \; 2>/dev/null | awk '{sum+=$1} END {print sum}')
    
    log_success "$tarball_count tarballs, $(format_size "$TOTAL_SIZE_BYTES")"
    
    # STEP 4: 估算上传时间
    log_step 4 "Estimated upload time..."
    local estimated_time
    estimated_time=$(estimate_duration "$TOTAL_SIZE_BYTES")
    log_success "~ $estimated_time @ 50 Mbps"
    
    # STEP 5: 上传
    log_step 5 "Uploading..."
    
    # 检查 bucket 是否存在
    if ! aws s3 ls "s3://${BUCKET}" $PROFILE_OPTION --region "$REGION" &>/dev/null; then
        log_error "S3 bucket 不存在: $BUCKET"
        exit 2
    fi
    
    # 构建 S3 路径
    local s3_prefix="${VENDOR_ID}/$(basename "$BATCH_DIR")/"
    local s3_uri="s3://${BUCKET}/${s3_prefix}"
    
    if [[ "$DRY_RUN" == true ]]; then
        echo "[DRY-RUN] 将执行以下命令:"
        echo "aws s3 sync \"$BATCH_DIR\" \"$s3_uri\" \\"
        echo "  --multipart-threshold 64MB \\"
        echo "  --multipart-chunksize 16MB \\"
        echo "  --storage-class STANDARD \\"
        echo "  --cli-read-timeout 0 \\"
        echo "  --cli-connect-timeout 60 \\"
        echo "  $PROFILE_OPTION \\"
        echo "  --region \"$REGION\""
        UPLOAD_SUCCESS=$tarball_count
        TOTAL_FILES=$tarball_count
    else
        # 执行上传
        # aws s3 sync 自带重试和进度条
        if aws s3 sync "$BATCH_DIR" "$s3_uri" \
            --multipart-threshold 64MB \
            --multipart-chunksize 16MB \
            --storage-class STANDARD \
            --cli-read-timeout 0 \
            --cli-connect-timeout 60 \
            $PROFILE_OPTION \
            --region "$REGION"; then
            
            UPLOAD_SUCCESS=$tarball_count
            TOTAL_FILES=$tarball_count
        else
            UPLOAD_FAILED=$tarball_count
            TOTAL_FILES=$tarball_count
            log_error "上传失败"
            exit 1
        fi
    fi
    
    # STEP 6: 验证
    log_step 6 "Verifying remote..."
    
    if [[ "$DRY_RUN" == true ]]; then
        log_success "[DRY-RUN] 跳过验证"
    else
        local verify_result
        if verify_result=$(verify_upload "$BATCH_DIR" "$BUCKET" "$s3_prefix" "$PROFILE_OPTION"); then
            local match_count
            match_count=$(echo "$verify_result" | cut -d: -f1)
            local total_count
            total_count=$(echo "$verify_result" | cut -d: -f2)
            log_success "$match_count/$total_count files match"
        else
            log_error "验证失败"
            exit 3
        fi
    fi
    
    # STEP 7: 完成
    log_step 7 "Done."
    
    if [[ "$DRY_RUN" == true ]]; then
        log_success "[DRY-RUN] 计划完成"
    else
        END_TIME=$(date +%s)
        local duration=$((END_TIME - START_TIME))
        local hours=$((duration / 3600))
        local minutes=$(((duration % 3600) / 60))
        
        # 估算费用
        local size_gb
        size_gb=$(echo "scale=4; $TOTAL_SIZE_BYTES / 1073741824" | bc)
        local cost
        cost=$(echo "scale=2; $size_gb * 0.02" | bc)
        
        log_success "Total: ${hours}h ${minutes}m | Cost: ~\$$cost transfer"
    fi
    
    exit 0
}

# ============================================================================
# 入口
# ============================================================================
parse_args "$@"
main