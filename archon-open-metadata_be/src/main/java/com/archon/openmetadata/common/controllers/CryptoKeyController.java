package com.archon.openmetadata.common.controllers;

import com.archon.openmetadata.common.utils.CryptoUtils;
import com.archon.openmetadata.job.models.ConnectionProfile;
import com.archon.openmetadata.job.repositories.ConnectionProfileRepository;
import java.util.List;
import java.util.Map;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1/system/crypto")
@RequiredArgsConstructor
@CrossOrigin(origins = "*")
@Slf4j
public class CryptoKeyController {

  private final CryptoUtils cryptoUtils;
  private final ConnectionProfileRepository connectionProfileRepository;

  @PostMapping("/rotate-key")
  public ResponseEntity<?> rotateKey(@RequestBody Map<String, String> request) {
    String newKey = request.get("newKey");
    if (newKey == null || newKey.trim().isEmpty()) {
      return ResponseEntity.badRequest().body("newKey is required");
    }

    String oldKey = cryptoUtils.getSecret();
    
    try {
      // 1. Fetch all connection profiles
      List<ConnectionProfile> profiles = connectionProfileRepository.findAll();
      
      // 2. Re-encrypt with new key
      for (ConnectionProfile cp : profiles) {
        if (cp.getPass() != null) {
          // Decrypt with current key
          String plainText = cryptoUtils.decrypt(cp.getPass(), oldKey);
          // Encrypt with new key
          String newCipher = cryptoUtils.encrypt(plainText, newKey);
          cp.setPass(newCipher);
        }
      }
      
      // 3. Save all updated profiles
      connectionProfileRepository.saveAll(profiles);
      
      // 4. Update the active key in memory
      cryptoUtils.setSecret(newKey);
      
      log.info("Successfully rotated crypto key and updated {} connection profiles.", profiles.size());
      
      return ResponseEntity.ok(Map.of(
          "message", "Key rotated successfully.",
          "profilesUpdated", profiles.size(),
          "note", "Please ensure you update app.crypto.secret in your application.yml to persist this change across restarts."
      ));
    } catch (Exception e) {
      log.error("Failed to rotate key: ", e);
      return ResponseEntity.internalServerError().body("Failed to rotate key: " + e.getMessage());
    }
  }
}
