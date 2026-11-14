"""把「设备 + 连接」组装成流程图数据(纯函数,不碰 COM)。

输入就是工况库里已有的两张表:
  * `connections`(设备 ↔ 流股,含 side/port/stage)
  * `blocks`(设备类型 + 焓平衡判定)

输出给前端画图用:
  * nodes:设备节点(类型 / 焓平衡状态 / ΔH)
  * links:设备 → 设备的边(同一条流股共用一个端点对,聚合后画一条曲线)
  * feeds / products:边界流股(没有上游 / 没有下游)

为什么要聚合:183 条连接里很多是"(E0101 → E0102) 的进出流股"成对出现,
画成两条重叠曲线既看不清也没意义;聚合成一条边、把流股名放在边里,悬停再看。
"""

from __future__ import annotations


def build(connections, blocks) -> dict:
    block_map = {b["name"]: dict(b) for b in (blocks or [])}
    producer: dict[str, str] = {}
    consumer: dict[str, str] = {}
    sides: dict[str, str] = {}

    for row in connections or []:
        stream = row["stream"]
        block = row["block"]
        if row.get("direction") == "out":
            producer.setdefault(stream, block)
        else:
            consumer.setdefault(stream, block)
        if row.get("side"):
            sides[stream] = row["side"]

    feeds: list[dict] = []
    products: list[dict] = []
    grouped: dict[tuple[str, str], dict] = {}
    for stream in sorted(set(producer) | set(consumer)):
        upstream = producer.get(stream, "")
        downstream = consumer.get(stream, "")
        if upstream and downstream:
            item = grouped.setdefault(
                (upstream, downstream),
                {"from": upstream, "to": downstream, "streams": [], "sides": []},
            )
            item["streams"].append(stream)
            if sides.get(stream):
                item["sides"].append(sides[stream])
        elif downstream:
            feeds.append({"stream": stream, "to": downstream})
        elif upstream:
            products.append({"stream": stream, "from": upstream})

    links = []
    for item in grouped.values():
        links.append(
            {
                "from": item["from"],
                "to": item["to"],
                "streams": item["streams"],
                "sides": sorted(set(item["sides"])),
            }
        )

    nodes = []
    for name, block in block_map.items():
        nodes.append(
            {
                "name": name,
                "type": block.get("type") or "",
                "state": block.get("balance_state") or "",
                "dh_kw": block.get("dh_kw"),
                "ref_kw": block.get("ref_kw"),
                "duty_kw": block.get("duty_kw"),
                "power_kw": block.get("power_kw"),
                "nstage": block.get("nstage"),
                "status": block.get("status"),
            }
        )

    connected = {link["from"] for link in links} | {link["to"] for link in links}
    connected |= {f["to"] for f in feeds}
    connected |= {p["from"] for p in products}

    return {
        "nodes": nodes,
        "links": links,
        "feeds": feeds,
        "products": products,
        "counts": {
            "nodes": len(nodes),
            "edges": len(links),
            "streams": len(producer) + len([s for s in consumer if s not in producer]),
            "feeds": len(feeds),
            "products": len(products),
            "isolated": len([n for n in nodes if n["name"] not in connected]),
        },
    }
