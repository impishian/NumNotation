#!/bin/bash

# 检查是否提供了参数
if [ $# -eq 0 ]; then
    echo "用法: $0 <num 文件>"
    echo "示例: $0 test"
    exit 1
fi

prefix="$1"

# 检查输入文件是否存在
if [ ! -f "${prefix}.num" ]; then
    echo "错误: 输入文件 ${prefix}.num 不存在"
    exit 1
fi

# 执行 guitar_ly.py 转换
python3 ./numnotation.py  "${prefix}.num"

# 检查是否成功生成 .ly 文件
if [ ! -s "${prefix}.ly" ]; then
    echo "错误: 生成 ${prefix}.ly 失败"
    exit 1
fi

# 生成 .pdf 文件
lilypond "${prefix}.ly"

# 检查是否成功生成 .pdf 文件
if [ ! -s "${prefix}.pdf" ]; then
    echo "错误: 生成 ${prefix}.pdf 失败"
    exit 1
fi

# 打开 .pdf 文件
open "${prefix}.pdf"
