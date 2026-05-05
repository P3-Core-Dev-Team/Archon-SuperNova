package com.archon.openmetadata.common.controllers;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/system-properties")
@CrossOrigin(origins = "*")
public class SystemPropertiesController {

    // Simple in-memory mock for system properties
    private final Map<String, String> properties = new HashMap<>();

    public SystemPropertiesController() {
        properties.put("dataCleanupDays", "30");
    }

    @GetMapping
    public ResponseEntity<List<Map<String, String>>> getAll() {
        List<Map<String, String>> result = new ArrayList<>();
        properties.forEach((k, v) -> {
            Map<String, String> p = new HashMap<>();
            p.put("propKey", k);
            p.put("propValue", v);
            result.add(p);
        });
        return ResponseEntity.ok(result);
    }

    @PostMapping
    public ResponseEntity<?> save(@RequestBody Map<String, String> payload) {
        String key = payload.get("propKey");
        String value = payload.get("propValue");
        if (key != null && value != null) {
            properties.put(key, value);
        }
        return ResponseEntity.ok().build();
    }
}
