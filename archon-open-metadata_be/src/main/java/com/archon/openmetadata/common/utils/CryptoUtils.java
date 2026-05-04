package com.archon.openmetadata.common.utils;

import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.security.spec.KeySpec;
import java.util.Base64;
import javax.crypto.Cipher;
import javax.crypto.SecretKey;
import javax.crypto.SecretKeyFactory;
import javax.crypto.spec.IvParameterSpec;
import javax.crypto.spec.PBEKeySpec;
import javax.crypto.spec.SecretKeySpec;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.springframework.beans.factory.annotation.Value;

@Component
@Slf4j
public class CryptoUtils {

  private static final String salt = "56c6b89e-00e1-4fef-8267-2b6837f0e721";
  
  @Value("${app.crypto.secret:archon-supernova-default-secret}")
  private String secret;

  public void setSecret(String secret) {
      this.secret = secret;
  }

  public String getSecret() {
      return this.secret;
  }

  public static String encryptBase64(String strToEncrypt) {
    return Base64.getEncoder().encodeToString(strToEncrypt.getBytes(StandardCharsets.UTF_8));
  }

  public static String decryptBase64(String strToEncrypt) {
    return new String(Base64.getDecoder().decode(strToEncrypt), StandardCharsets.UTF_8);
  }

  public String encrypt(String strToEncrypt, String overrideKey) throws Exception {
    try {
      String actualSecret = overrideKey != null ? overrideKey : secret;
      
      byte[] iv = new byte[16];
      SecureRandom secRandom = new SecureRandom();
      secRandom.nextBytes(iv);
      IvParameterSpec ivspec = new IvParameterSpec(iv);
      SecretKeyFactory factory = SecretKeyFactory.getInstance("PBKDF2WithHmacSHA512");
      KeySpec spec = new PBEKeySpec(actualSecret.toCharArray(), salt.getBytes(StandardCharsets.UTF_8), 200000, 256);
      SecretKey tmp = factory.generateSecret(spec);
      SecretKeySpec secretKeySpec = new SecretKeySpec(tmp.getEncoded(), "AES");
      Cipher cipher = Cipher.getInstance("AES/CBC/PKCS5Padding");
      cipher.init(Cipher.ENCRYPT_MODE, secretKeySpec, ivspec);
      
      byte[] cipherText = cipher.doFinal(strToEncrypt.getBytes(StandardCharsets.UTF_8));
      byte[] cipherTextWithIv = new byte[iv.length + cipherText.length];
      System.arraycopy(iv, 0, cipherTextWithIv, 0, iv.length);
      System.arraycopy(cipherText, 0, cipherTextWithIv, iv.length, cipherText.length);
      
      return Base64.getEncoder().encodeToString(cipherTextWithIv);
    } catch (Exception e) {
      log.error("Error while encrypting: {}", e.getMessage());
      throw new Exception(e.getMessage());
    }
  }

  public String decrypt(String strToDecrypt, String overrideKey) throws Exception {
    try {
      String actualSecret = overrideKey != null ? overrideKey : secret;

      byte[] cipherTextWithIv = Base64.getDecoder().decode(strToDecrypt);
      byte[] iv = new byte[16];
      System.arraycopy(cipherTextWithIv, 0, iv, 0, iv.length);
      IvParameterSpec ivspec = new IvParameterSpec(iv);
      
      byte[] cipherText = new byte[cipherTextWithIv.length - iv.length];
      System.arraycopy(cipherTextWithIv, iv.length, cipherText, 0, cipherText.length);
      
      SecretKeyFactory factory = SecretKeyFactory.getInstance("PBKDF2WithHmacSHA512");
      KeySpec spec = new PBEKeySpec(actualSecret.toCharArray(), salt.getBytes(StandardCharsets.UTF_8), 200000, 256);
      SecretKey tmp = factory.generateSecret(spec);
      SecretKeySpec secretKeySpec = new SecretKeySpec(tmp.getEncoded(), "AES");
      Cipher cipher = Cipher.getInstance("AES/CBC/PKCS5Padding");
      cipher.init(Cipher.DECRYPT_MODE, secretKeySpec, ivspec);
      
      return new String(cipher.doFinal(cipherText), StandardCharsets.UTF_8);
    } catch (Exception e) {
      log.error("Error while decrypting: {}", e.getMessage());
      throw new Exception(e.getMessage());
    }
  }
}
